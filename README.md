# YouTube Clipper API

An async FastAPI service for downloading and clipping YouTube videos using yt-dlp, with RabbitMQ task queuing and MinIO S3 storage.

## Features

- **Async FastAPI** - High-performance async web framework
- **yt-dlp** - Download YouTube videos in best quality
- **ffmpeg** - Clip videos with precise time intervals
- **RabbitMQ** - Persistent message queue with dead letter queue (DLQ)
- **MinIO S3** - Object storage for video clips
- **PostgreSQL** - Job tracking and status management
- **API Key Authentication** - Secure endpoints
- **Kubernetes Ready** - HPA for auto-scaling workers
- **Docker Compose** - Easy local development setup

## Architecture

```
┌─────────────┐      ┌──────────────┐      ┌──────────────┐
│   Client    │─────▶│  FastAPI API │─────▶│   RabbitMQ   │
└─────────────┘      └──────────────┘      └──────┬───────┘
                            │                      │
                            ▼                      ▼
                     ┌──────────────┐      ┌──────────────┐
                     │  PostgreSQL  │◀─────│   Workers    │
                     └──────────────┘      └──────┬───────┘
                                                   │
                                                   ▼
                                            ┌──────────────┐
                                            │  MinIO S3    │
                                            └──────────────┘
```

## Prerequisites

- Docker and Docker Compose
- (Optional) Kubernetes cluster for production deployment

## Quick Start

### Local Development with Docker Compose

1. **Clone the repository**
   ```bash
   cd yt-clipper
   ```

2. **Create environment file**
   ```bash
   cp .env.example .env
   # Edit .env and change API_KEY for production
   ```

3. **Start all services**
   ```bash
   cd support
   docker-compose up --build
   ```

4. **Wait for services to be ready**
   - API: http://localhost:8000
   - RabbitMQ Management: http://localhost:15672 (guest/guest)
   - MinIO Console: http://localhost:9001 (minioadmin/minioadmin)

5. **Create a clip job**
   ```bash
   curl -X POST http://localhost:8000/clip \
     -H "Content-Type: application/json" \
     -H "X-API-Key: dev-api-key-change-in-production" \
     -d '{
       "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
       "start_time": 10.0,
       "end_time": 30.0
     }'
   ```

6. **Check job status**
   ```bash
   curl http://localhost:8000/clip/{job_id}/status \
     -H "X-API-Key: dev-api-key-change-in-production"
   ```

## API Documentation

Once the API is running, visit:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### Endpoints

#### `POST /clip`
Create a new clip job.

**Headers:**
- `X-API-Key`: Your API key

**Request Body:**
```json
{
  "video_url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "start_time": 10.0,
  "end_time": 30.0
}
```

**Response:**
```json
{
  "job_id": "uuid-here",
  "status": "pending",
  "message": "Clip job created and queued for processing"
}
```

**Constraints:**
- `start_time` must be >= 0
- `end_time` must be > `start_time`
- Max clip duration: 10 minutes (600 seconds)

#### `GET /clip/{job_id}/status`
Get the status of a clip job.

**Headers:**
- `X-API-Key`: Your API key

**Response:**
```json
{
  "job_id": "uuid-here",
  "video_url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "start_time": 10.0,
  "end_time": 30.0,
  "status": "completed",
  "s3_url": "http://minio:9000/clips/uuid-here/clip_uuid-here.mp4",
  "error_message": null,
  "created_at": "2025-11-22T10:00:00",
  "updated_at": "2025-11-22T10:05:00"
}
```

**Status Values:**
- `pending` - Job queued, waiting for worker
- `processing` - Worker is processing the job
- `completed` - Clip successfully created and uploaded
- `failed` - Job failed after max retry attempts

## Kubernetes Deployment

### Prerequisites

- Kubernetes cluster (minikube, GKE, EKS, AKS, etc.)
- kubectl configured
- Docker image built and pushed to registry

### Build and Push Docker Image

```bash
# Build the image
docker build -t your-registry/yt-clipper:latest .

# Push to registry
docker push your-registry/yt-clipper:latest
```

### Deploy to Kubernetes

1. **Update image in deployments**
   ```bash
   # Edit k8s/api-deployment.yaml and k8s/worker-deployment.yaml
   # Change image: yt-clipper:latest to your-registry/yt-clipper:latest
   ```

2. **Update secrets**
   ```bash
   # Edit k8s/secrets.yaml with production values
   # IMPORTANT: Base64 encode sensitive values or use sealed-secrets
   ```

3. **Apply manifests**
   ```bash
   kubectl apply -f k8s/namespace.yaml
   kubectl apply -f k8s/configmap.yaml
   kubectl apply -f k8s/secrets.yaml
   kubectl apply -f k8s/pvc.yaml
   kubectl apply -f k8s/postgres.yaml
   kubectl apply -f k8s/rabbitmq.yaml
   kubectl apply -f k8s/minio.yaml
   kubectl apply -f k8s/api-deployment.yaml
   kubectl apply -f k8s/worker-deployment.yaml
   ```

4. **Check deployment status**
   ```bash
   kubectl get pods -n yt-clipper
   kubectl get services -n yt-clipper
   ```

5. **Access the API**
   ```bash
   # Get LoadBalancer external IP
   kubectl get service clipper-api-service -n yt-clipper
   ```

### Horizontal Pod Autoscaler (HPA)

The worker deployment includes HPA configuration:
- **Min replicas:** 2
- **Max replicas:** 10
- **CPU threshold:** 70%
- **Memory threshold:** 80%

HPA will automatically scale workers based on load. Ensure metrics-server is installed:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

## Configuration

All configuration is done via environment variables. See `.env.example` for all available options.

### Key Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEY` | - | API key for authentication (required) |
| `MAX_CLIP_DURATION_SECONDS` | 600 | Maximum clip duration in seconds |
| `DATABASE_URL` | - | PostgreSQL connection string |
| `RABBITMQ_URL` | - | RabbitMQ connection string |
| `S3_ENDPOINT_URL` | - | MinIO/S3 endpoint URL |
| `WORKER_CONCURRENCY` | 2 | Number of concurrent tasks per worker |
| `MAX_RETRY_ATTEMPTS` | 2 | Maximum retry attempts before DLQ |

## Monitoring

### RabbitMQ Management UI
- URL: http://localhost:15672 (local) or NodePort 31672 (k8s)
- Username: guest
- Password: guest

Monitor:
- Queue depth
- Message rates
- Consumer count
- DLQ messages

### MinIO Console
- URL: http://localhost:9001 (local) or NodePort 31001 (k8s)
- Username: minioadmin
- Password: minioadmin

View:
- Stored clips
- Bucket usage
- Upload statistics

### Logs

**Docker Compose:**
```bash
docker-compose -f support/docker-compose.yml logs -f api
docker-compose -f support/docker-compose.yml logs -f worker-1
```

**Kubernetes:**
```bash
kubectl logs -f -n yt-clipper -l app=clipper-api
kubectl logs -f -n yt-clipper -l app=clipper-worker
```

## Error Handling

### Retry Logic
- Failed jobs are automatically retried up to 2 times
- After max retries, jobs are moved to Dead Letter Queue (DLQ)
- Job status in database reflects current state

### Dead Letter Queue
Messages in DLQ can be:
1. Manually inspected via RabbitMQ Management UI
2. Re-queued after fixing issues
3. Purged if unrecoverable

## Development

### Project Structure

```
yt-clipper/
├── app/
│   ├── __init__.py
│   ├── config.py          # Configuration settings
│   ├── database.py        # Database models and connection
│   ├── main.py            # FastAPI application
│   └── models.py          # Pydantic models
├── worker/
│   ├── __init__.py
│   └── consumer.py        # RabbitMQ consumer worker
├── k8s/                   # Kubernetes manifests
│   ├── namespace.yaml
│   ├── configmap.yaml
│   ├── secrets.yaml
│   ├── pvc.yaml
│   ├── postgres.yaml
│   ├── rabbitmq.yaml
│   ├── minio.yaml
│   ├── api-deployment.yaml
│   └── worker-deployment.yaml
├── support/
│   └── docker-compose.yml # Local development setup
├── Dockerfile             # Multi-stage Docker build
├── requirements.txt       # Python dependencies
├── .env.example          # Example environment variables
└── README.md
```

### Running Tests

```bash
# Install dev dependencies
pip install pytest pytest-asyncio httpx

# Run tests
pytest
```

## Troubleshooting

### Workers not processing jobs
1. Check RabbitMQ connection: `docker-compose logs rabbitmq`
2. Check worker logs: `docker-compose logs worker-1`
3. Verify queue exists in RabbitMQ Management UI

### Video download fails
1. Verify video URL is accessible
2. Check yt-dlp version: `docker-compose exec worker-1 yt-dlp --version`
3. Update yt-dlp: `pip install -U yt-dlp` and rebuild

### S3 upload fails
1. Check MinIO is running: `docker-compose ps minio`
2. Verify credentials in .env
3. Check bucket exists in MinIO Console

### Database connection issues
1. Wait for PostgreSQL to be ready (health check)
2. Verify DATABASE_URL is correct
3. Check PostgreSQL logs: `docker-compose logs postgres`

## License

MIT

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.
