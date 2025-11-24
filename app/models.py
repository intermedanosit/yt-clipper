from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime


class ClipRequest(BaseModel):
    video_url: str = Field(..., description="YouTube video URL")
    start_time: float = Field(..., ge=0, description="Start time in seconds")
    end_time: float = Field(..., gt=0, description="End time in seconds")
    
    @field_validator('end_time')
    @classmethod
    def validate_times(cls, end_time: float, info) -> float:
        start_time = info.data.get('start_time', 0)
        if end_time <= start_time:
            raise ValueError("end_time must be greater than start_time")
        
        duration = end_time - start_time
        if duration > 600:  # 10 minutes
            raise ValueError("Clip duration cannot exceed 10 minutes (600 seconds)")
        
        return end_time


class ClipResponse(BaseModel):
    job_id: str
    status: str
    message: str


class ClipStatus(BaseModel):
    job_id: str = Field(..., alias='id')
    video_url: str
    start_time: float
    end_time: float
    status: str
    s3_url: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True
        populate_by_name = True
