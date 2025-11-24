#!/bin/bash

# YouTube Clipper - Local Development Startup Script
# This script starts all services using docker-compose

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}Starting YouTube Clipper local environment...${NC}\n"

# Set the path to the docker-compose file
COMPOSE_FILE="$(dirname "$0")/support/docker-compose.yml"

# Check if docker is running
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}Error: Docker is not running. Please start Docker and try again.${NC}"
    exit 1
fi

# Check if docker-compose is available
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null 2>&1; then
    echo -e "${RED}Error: docker-compose is not installed.${NC}"
    exit 1
fi

# Use docker compose if available, otherwise docker-compose
if docker compose version &> /dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
else
    COMPOSE_CMD="docker-compose"
fi

echo -e "${YELLOW}Using: $COMPOSE_CMD${NC}\n"

# Pull latest images
echo -e "${GREEN}Pulling latest images...${NC}"
$COMPOSE_CMD -f "$COMPOSE_FILE" pull

# Build the application images
echo -e "\n${GREEN}Building application images...${NC}"
$COMPOSE_CMD -f "$COMPOSE_FILE" build

# Start all services
echo -e "\n${GREEN}Starting all services...${NC}"
$COMPOSE_CMD -f "$COMPOSE_FILE" up -d

# Wait a moment for services to initialize
echo -e "\n${YELLOW}Waiting for services to initialize...${NC}"
sleep 5

# Show status
echo -e "\n${GREEN}Service Status:${NC}"
$COMPOSE_CMD -f "$COMPOSE_FILE" ps

echo -e "\n${GREEN}✓ YouTube Clipper is starting up!${NC}"
echo -e "\nServices available at:"
echo -e "  - API:              http://localhost:8000"
echo -e "  - API Docs:         http://localhost:8000/docs"
echo -e "  - RabbitMQ UI:      http://localhost:15672 (guest/guest)"
echo -e "  - MinIO Console:    http://localhost:9001 (minioadmin/minioadmin)"
echo -e "\nView logs with: ${YELLOW}$COMPOSE_CMD -f \"$COMPOSE_FILE\" logs -f${NC}"
echo -e "Stop services with: ${YELLOW}$COMPOSE_CMD -f \"$COMPOSE_FILE\" down${NC}"
