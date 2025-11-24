import asyncio
import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from datetime import datetime
from typing import Optional

from aio_pika import connect_robust, IncomingMessage, ExchangeType
from aio_pika.abc import AbstractIncomingMessage
import aioboto3
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, update

from app.config import get_settings
from app.database import ClipJob, Base

settings = get_settings()

# Create async engine for worker
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class VideoClipper:
    """Handles video downloading and clipping operations"""
    
    def __init__(self):
        self.temp_dir = Path(settings.temp_download_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
    
    async def download_video(self, video_url: str, output_path: str) -> str:
        """Download video using yt-dlp"""
        cmd = [
            "yt-dlp",
            "-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]/best",
            "--merge-output-format", "mp4",
            "--extractor-args", "youtube:player_client=default,web_safari;player_js_version=actual",
            "-o", output_path,
            video_url
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            raise Exception(f"yt-dlp failed: {stderr.decode()}")
        
        return output_path
    
    async def clip_video(self, input_path: str, output_path: str, start_time: float, end_time: float):
        """Clip video using ffmpeg"""
        duration = end_time - start_time
        
        cmd = [
            "ffmpeg",
            "-ss", str(start_time),
            "-i", input_path,
            "-t", str(duration),
            "-c", "copy",
            "-avoid_negative_ts", "1",
            output_path,
            "-y"  # Overwrite output file if exists
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            raise Exception(f"ffmpeg failed: {stderr.decode()}")
        
        return output_path
    
    async def upload_to_s3(self, file_path: str, job_id: str) -> str:
        """Upload file to MinIO S3"""
        filename = Path(file_path).name
        s3_key = f"clips/{job_id}/{filename}"
        
        session = aioboto3.Session()
        async with session.client(
            's3',
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region
        ) as s3:
            # Ensure bucket exists
            try:
                await s3.head_bucket(Bucket=settings.s3_bucket)
            except:
                await s3.create_bucket(Bucket=settings.s3_bucket)
            
            # Upload file
            with open(file_path, 'rb') as f:
                await s3.upload_fileobj(
                    f,
                    settings.s3_bucket,
                    s3_key,
                    ExtraArgs={'ContentType': 'video/mp4'}
                )
            
            # Generate S3 URL
            s3_url = f"{settings.s3_endpoint_url}/{settings.s3_bucket}/{s3_key}"
            return s3_url


class ClipWorker:
    """Consumer worker that processes clip jobs from RabbitMQ"""
    
    def __init__(self):
        self.clipper = VideoClipper()
        self.running = False
    
    async def update_job_status(
        self,
        job_id: str,
        status: str,
        s3_url: Optional[str] = None,
        error_message: Optional[str] = None,
        increment_retry: bool = False
    ):
        """Update job status in database"""
        async with async_session_maker() as session:
            stmt = select(ClipJob).where(ClipJob.id == job_id)
            result = await session.execute(stmt)
            job = result.scalar_one_or_none()
            
            if job:
                job.status = status
                job.updated_at = datetime.utcnow()
                
                if s3_url:
                    job.s3_url = s3_url
                if error_message:
                    job.error_message = error_message
                if increment_retry:
                    job.retry_count += 1
                
                await session.commit()
    
    async def process_message(self, message: AbstractIncomingMessage):
        """Process a single clip job message"""
        async with message.process(ignore_processed=True):
            try:
                # Parse message
                body = json.loads(message.body.decode())
                job_id = body["job_id"]
                video_url = body["video_url"]
                start_time = body["start_time"]
                end_time = body["end_time"]
                
                # Get retry count from headers
                retry_count = message.headers.get("x-retry-count", 0) if message.headers else 0
                
                print(f"Processing job {job_id} (attempt {retry_count + 1})", flush=True)
                
                # Update status to processing
                await self.update_job_status(job_id, "processing")
                
                # Create temporary directory for this job
                with tempfile.TemporaryDirectory(dir=settings.temp_download_dir) as temp_dir:
                    temp_path = Path(temp_dir)
                    
                    # Download video
                    print(f"Downloading video for job {job_id}", flush=True)
                    downloaded_file = temp_path / "video.mp4"
                    await self.clipper.download_video(video_url, str(downloaded_file))
                    
                    # Clip video
                    print(f"Clipping video for job {job_id}", flush=True)
                    clipped_file = temp_path / f"clip_{job_id}.mp4"
                    await self.clipper.clip_video(
                        str(downloaded_file),
                        str(clipped_file),
                        start_time,
                        end_time
                    )
                    
                    # Upload to S3
                    print(f"Uploading clip for job {job_id}", flush=True)
                    s3_url = await self.clipper.upload_to_s3(str(clipped_file), job_id)
                    
                    # Update job as completed
                    await self.update_job_status(job_id, "completed", s3_url=s3_url)
                    print(f"Job {job_id} completed successfully", flush=True)
                
                # Acknowledge message
                await message.ack()
                
            except Exception as e:
                error_msg = str(e)
                print(f"Error processing job: {error_msg}", file=sys.stderr, flush=True)
                traceback.print_exc(file=sys.stderr)
                
                # Check retry count
                retry_count = message.headers.get("x-retry-count", 0) if message.headers else 0
                
                if retry_count < settings.max_retry_attempts:
                    # Reject and requeue with incremented retry count
                    print(f"Rejecting message for retry (attempt {retry_count + 1}/{settings.max_retry_attempts})", flush=True)
                    await self.update_job_status(
                        body.get("job_id", "unknown"),
                        "pending",
                        error_message=error_msg,
                        increment_retry=True
                    )
                    
                    # Update retry count header
                    message.headers["x-retry-count"] = retry_count + 1
                    
                    # Reject without requeue - will go to DLQ due to x-dead-letter-exchange
                    await message.reject(requeue=False)
                else:
                    # Max retries reached, mark as failed and send to DLQ
                    print(f"Max retries reached for job, marking as failed", flush=True)
                    await self.update_job_status(
                        body.get("job_id", "unknown"),
                        "failed",
                        error_message=f"Failed after {settings.max_retry_attempts} attempts: {error_msg}"
                    )
                    await message.reject(requeue=False)
    
    async def start(self):
        """Start consuming messages from RabbitMQ"""
        self.running = True
        
        print(f"Connecting to RabbitMQ at {settings.rabbitmq_url}", flush=True)
        connection = await connect_robust(settings.rabbitmq_url)
        
        async with connection:
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=settings.worker_concurrency)
            
            # Declare exchange
            exchange = await channel.declare_exchange(
                settings.rabbitmq_exchange,
                ExchangeType.DIRECT,
                durable=True
            )
            
            # Declare main queue with DLX
            queue = await channel.declare_queue(
                settings.rabbitmq_queue,
                durable=True,
                arguments={
                    "x-dead-letter-exchange": f"{settings.rabbitmq_exchange}.dlx",
                    "x-dead-letter-routing-key": settings.rabbitmq_dlq,
                }
            )
            
            await queue.bind(exchange, routing_key=settings.rabbitmq_queue)
            
            # Declare DLQ exchange and queue
            dlx_exchange = await channel.declare_exchange(
                f"{settings.rabbitmq_exchange}.dlx",
                ExchangeType.DIRECT,
                durable=True
            )
            
            dlq = await channel.declare_queue(
                settings.rabbitmq_dlq,
                durable=True
            )
            
            await dlq.bind(dlx_exchange, routing_key=settings.rabbitmq_dlq)
            
            print(f"Worker started, consuming from queue: {settings.rabbitmq_queue}", flush=True)
            print(f"Concurrency: {settings.worker_concurrency}", flush=True)
            
            # Start consuming
            async with queue.iterator() as queue_iter:
                async for message in queue_iter:
                    if not self.running:
                        break
                    await self.process_message(message)


async def main():
    """Main entry point for worker"""
    worker = ClipWorker()
    
    try:
        await worker.start()
    except KeyboardInterrupt:
        print("Worker stopped by user", flush=True)
    except Exception as e:
        print(f"Worker error: {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        raise


if __name__ == "__main__":
    asyncio.run(main())
