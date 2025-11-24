from fastapi import FastAPI, Depends, HTTPException, Header, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid
import json
from contextlib import asynccontextmanager
from aio_pika import connect_robust, Message, DeliveryMode, ExchangeType

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
    version="1.0.0",
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
    return {"message": "YouTube Clipper API", "version": "1.0.0"}


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


@app.get("/clip/{job_id}/status", response_model=ClipStatus, dependencies=[Depends(verify_api_key)])
async def get_clip_status(
    job_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get the status of a clip job by job ID.
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
    
    return ClipStatus.model_validate(job)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
