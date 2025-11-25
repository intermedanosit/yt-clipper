from fastapi import FastAPI, Depends, HTTPException, Header, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid
import json
from contextlib import asynccontextmanager
from aio_pika import connect_robust, Message, DeliveryMode, ExchangeType
import aioboto3
from botocore.config import Config

from app.config import get_settings
from app.database import get_db, init_db, ClipJob
from app.models import ClipRequest, ClipResponse, ClipStatus

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize database
    await init_db()
    yield
    # Shutdown: cleanup if needed


app = FastAPI(
    title="YouTube Clipper API",
    description="Async API for downloading and clipping YouTube videos",
    version="1.0.1",
    lifespan=lifespan
)


async def verify_api_key(x_api_key: str = Header(...)):
    """API Key authentication middleware"""
    if x_api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key"
        )
    return x_api_key


@app.get("/")
async def root():
    return {"message": "YouTube Clipper API", "version": "1.0.1"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/clip", response_model=ClipResponse, dependencies=[Depends(verify_api_key)])
async def create_clip(
    request: ClipRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new video clip job. Returns job ID immediately.
    The clip will be processed asynchronously.
    """
    # Generate unique job ID
    job_id = str(uuid.uuid4())
    
    # Create job record in database
    job = ClipJob(
        id=job_id,
        video_url=request.video_url,
        start_time=request.start_time,
        end_time=request.end_time,
        status="pending"
    )
    
    db.add(job)
    await db.commit()
    
    # Publish message to RabbitMQ
    try:
        connection = await connect_robust(settings.rabbitmq_url)
        async with connection:
            channel = await connection.channel()
            
            # Declare exchange
            exchange = await channel.declare_exchange(
                settings.rabbitmq_exchange,
                ExchangeType.DIRECT,
                durable=True
            )
            
            # Declare main queue
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
            
            # Publish message
            message_body = {
                "job_id": job_id,
                "video_url": request.video_url,
                "start_time": request.start_time,
                "end_time": request.end_time
            }
            
            message = Message(
                body=json.dumps(message_body).encode(),
                delivery_mode=DeliveryMode.PERSISTENT,
                headers={"x-retry-count": 0}
            )
            
            await exchange.publish(
                message,
                routing_key=settings.rabbitmq_queue
            )
            
    except Exception as e:
        # Update job status to failed
        job.status = "failed"
        job.error_message = f"Failed to queue job: {str(e)}"
        await db.commit()
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to queue clip job: {str(e)}"
        )
    
    return ClipResponse(
        job_id=job_id,
        status="pending",
        message="Clip job created and queued for processing"
    )


async def generate_presigned_url(s3_key: str, expiration: int = 3600) -> str:
    """Generate a presigned URL for downloading a file from S3"""
    session = aioboto3.Session()
    
    boto_config = Config(
        signature_version='s3v4',
        s3={'addressing_style': 'path'}
    )
    
    use_ssl = settings.s3_public_endpoint_url.startswith('https://')
    
    async with session.client(
        's3',
        endpoint_url=settings.s3_public_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        use_ssl=use_ssl,
        config=boto_config,
        verify=False if not use_ssl else None
    ) as s3:
        presigned_url = await s3.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': settings.s3_bucket,
                'Key': s3_key
            },
            ExpiresIn=expiration
        )
        return presigned_url


@app.get("/clip/{job_id}/status", response_model=ClipStatus, dependencies=[Depends(verify_api_key)])
async def get_clip_status(
    job_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get the status of a clip job by job ID.
    Returns a presigned URL for completed clips (valid for 1 hour).
    """
    result = await db.execute(
        select(ClipJob).where(ClipJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found"
        )
    
    # Generate presigned URL if clip is completed and has S3 key
    download_url = None
    if job.status == "completed" and job.s3_key:
        download_url = await generate_presigned_url(job.s3_key)
    
    # Build response
    response_data = {
        "id": job.id,
        "video_url": job.video_url,
        "start_time": job.start_time,
        "end_time": job.end_time,
        "status": job.status,
        "download_url": download_url,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "updated_at": job.updated_at
    }
    
    return ClipStatus.model_validate(response_data)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
