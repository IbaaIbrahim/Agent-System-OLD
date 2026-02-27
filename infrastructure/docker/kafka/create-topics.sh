#!/bin/bash
# Kafka topic creation script
# Runs after Kafka broker is healthy

set -e

# Use environment variable if set, otherwise default to kafka:29092
KAFKA_BOOTSTRAP_SERVERS="${KAFKA_BOOTSTRAP_SERVER:-kafka:29092}"

echo "Waiting for Kafka to be fully ready..."
sleep 10

echo "Creating Kafka topics..."

# Main job queue - partitioned for parallel processing
kafka-topics --create \
    --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS \
    --topic agent.jobs \
    --partitions 6 \
    --replication-factor 1 \
    --config retention.ms=604800000 \
    --config cleanup.policy=delete \
    --if-not-exists

# Tool execution requests
kafka-topics --create \
    --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS \
    --topic agent.tools \
    --partitions 3 \
    --replication-factor 1 \
    --config retention.ms=86400000 \
    --if-not-exists

# Tool execution results
kafka-topics --create \
    --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS \
    --topic agent.tool-results \
    --partitions 3 \
    --replication-factor 1 \
    --config retention.ms=86400000 \
    --if-not-exists

# Dead letter queue for failed jobs
kafka-topics --create \
    --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS \
    --topic agent.jobs.dlq \
    --partitions 1 \
    --replication-factor 1 \
    --config retention.ms=2592000000 \
    --if-not-exists

# Dead letter queue for failed tool executions
kafka-topics --create \
    --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS \
    --topic agent.tools.dlq \
    --partitions 1 \
    --replication-factor 1 \
    --config retention.ms=2592000000 \
    --if-not-exists

# Job resumption signals from tool workers
kafka-topics --create \
    --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS \
    --topic agent.job-resume \
    --partitions 6 \
    --replication-factor 1 \
    --config retention.ms=3600000 \
    --if-not-exists

# User confirmation responses for CONFIRM_REQUIRED tools
kafka-topics --create \
    --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS \
    --topic agent.confirm \
    --partitions 3 \
    --replication-factor 1 \
    --config retention.ms=3600000 \
    --if-not-exists

# User responses to agent questions (human-in-the-loop)
kafka-topics --create \
    --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS \
    --topic agent.user-response \
    --partitions 3 \
    --replication-factor 1 \
    --config retention.ms=3600000 \
    --if-not-exists

# Events for archiving (compact for deduplication)
kafka-topics --create \
    --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS \
    --topic agent.events \
    --partitions 3 \
    --replication-factor 1 \
    --config retention.ms=604800000 \
    --config cleanup.policy=compact,delete \
    --if-not-exists

echo "Listing all topics..."
kafka-topics --list --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS

echo "Topic creation complete!"
