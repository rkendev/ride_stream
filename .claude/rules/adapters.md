# Adapter Rules

## Core Principle
Adapters are the only layer allowed to talk to external systems. Every port MUST have TWO implementations: local (Docker Compose stack) and AWS (production). This enables seamless switching between dev and production without changing domain or application logic.

## Dual-Target Requirement (Rule 2)
Every external port must have two adapters implemented simultaneously. NO "we'll add AWS later."

RideStream v2 uses hexagonal architecture with dual-target design:
- **Local Adapters**: Docker Compose services (Kafka, MinIO, Trino, Spark, Airflow)
- **AWS Adapters**: Production cloud services (MSK, S3, Athena, Glue, EMR, Step Functions)

Each port has exactly two implementations selected via CloudProvider factory.

---

## Port & Adapter Overview

### EventBusPort (publish/consume events)
- **Local**: KafkaEventBus (confluentinc/cp-kafka:7.6.0 via rs2-kafka)
- **AWS**: MskEventBus (Amazon Managed Streaming for Kafka)
- **Client**: kafka-python >= 2.0.2
- **Error Map**: kafka.errors → EventBusError

### StoragePort (object storage)
- **Local**: MinioStorage (minio/minio via rs2-minio)
- **AWS**: S3Storage (boto3 s3 client)
- **Client Libraries**: minio >= 7.2.0, boto3 >= 1.28.0
- **Error Map**: minio.error, botocore.exceptions → StorageError

### QueryPort (SQL execution)
- **Local**: TrinoQuery (trinodb/trino:430 via rs2-trino)
- **AWS**: AthenaQuery (boto3 athena client)
- **Client Libraries**: sqlalchemy >= 2.0, boto3 >= 1.28.0
- **Error Map**: trino exceptions, botocore → RepositoryError

### DbtPort (dbt model execution)
- **Local**: DbtTrinoAdapter (dbt-trino >= 1.7.0)
- **AWS**: DbtAthenaAdapter (dbt-athena via Step Functions)
- **Client**: dbt-core >= 1.7.0
- **Error Map**: DbtRunFailedError

---

## Port Definitions

```python
# src/adapters/ports/event_bus.py
from abc import ABC, abstractmethod
from typing import List, Dict, Any, AsyncIterator
from datetime import datetime

class EventBusPort(ABC):
    """Abstraction for event streaming (Kafka/MSK)."""
    
    @abstractmethod
    async def publish_event(self, topic: str, key: str, value: Dict[str, Any]) -> None:
        """Publish event to topic."""
        pass
    
    @abstractmethod
    async def consume_events(
        self,
        topics: List[str],
        group_id: str,
        timeout_ms: int = 5000
    ) -> AsyncIterator[Dict[str, Any]]:
        """Consume events from topics as async generator."""
        pass
    
    @abstractmethod
    async def close(self) -> None:
        """Close connections."""
        pass


# src/adapters/ports/storage.py
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class StoragePort(ABC):
    """Abstraction for object storage (MinIO/S3)."""
    
    @abstractmethod
    async def put_object(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream"
    ) -> str:
        """Upload object. Returns object URL."""
        pass
    
    @abstractmethod
    async def get_object(self, bucket: str, key: str) -> bytes:
        """Download object."""
        pass
    
    @abstractmethod
    async def list_objects(self, bucket: str, prefix: str = "") -> List[str]:
        """List objects in bucket with prefix."""
        pass
    
    @abstractmethod
    async def delete_object(self, bucket: str, key: str) -> None:
        """Delete object."""
        pass


# src/adapters/ports/query.py
from abc import ABC, abstractmethod
from typing import List, Dict, Any

class QueryPort(ABC):
    """Abstraction for SQL queries (Trino/Athena)."""
    
    @abstractmethod
    async def execute_query(
        self,
        sql: str,
        parameters: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        """Execute SQL query, return rows as dicts."""
        pass
    
    @abstractmethod
    async def execute_query_streaming(
        self,
        sql: str,
        parameters: Dict[str, Any] = None
    ):
        """Execute query with streaming result (for large datasets)."""
        pass


# src/adapters/ports/dbt.py
from abc import ABC, abstractmethod
from typing import Dict, Any, List

class DbtPort(ABC):
    """Abstraction for dbt model execution."""
    
    @abstractmethod
    async def run_model(
        self,
        model_name: str,
        variables: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Run dbt model, return execution result."""
        pass
    
    @abstractmethod
    async def test_model(self, model_name: str) -> bool:
        """Run dbt tests for model, return success."""
        pass
```

---

## Local Adapters (Docker Compose)

### EventBusPort: Local Kafka Adapter

```python
# src/adapters/event_bus/local.py
import json
from typing import List, Dict, Any, AsyncIterator
from kafka import KafkaProducer, KafkaConsumer
from kafka.errors import KafkaError, KafkaTimeoutError
from loguru import logger
import asyncio

from .port import EventBusPort
from src.domain.exceptions import EventBusError


class KafkaEventBus(EventBusPort):
    """Local Kafka adapter using kafka-python client."""
    
    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        timeout_seconds: int = 30,
        max_retries: int = 3,
        batch_size: int = 100
    ):
        self.bootstrap_servers = bootstrap_servers
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.batch_size = batch_size
        
        self.producer: KafkaProducer = None
        self.consumer: KafkaConsumer = None
    
    async def publish_event(self, topic: str, key: str, value: Dict[str, Any]) -> None:
        """Publish event to Kafka topic with retry logic."""
        if not self.producer:
            self._initialize_producer()
        
        message_bytes = json.dumps(value).encode("utf-8")
        
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(
                    "kafka_publish_start",
                    extra={
                        "topic": topic,
                        "key": key,
                        "attempt": attempt,
                        "retry_max": self.max_retries
                    }
                )
                
                # Send with timeout and wait for confirmation
                future = self.producer.send(
                    topic,
                    key=key.encode("utf-8"),
                    value=message_bytes
                )
                
                # Block until message is sent (with timeout)
                record_metadata = future.get(timeout=self.timeout_seconds)
                
                logger.info(
                    "kafka_publish_success",
                    extra={
                        "topic": record_metadata.topic,
                        "partition": record_metadata.partition,
                        "offset": record_metadata.offset
                    }
                )
                return
            
            except KafkaTimeoutError as e:
                logger.warning(
                    "kafka_publish_timeout",
                    extra={
                        "attempt": attempt,
                        "timeout_sec": self.timeout_seconds,
                        "will_retry": attempt < self.max_retries
                    }
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                else:
                    raise EventBusError(f"Kafka publish timeout after {self.max_retries} retries") from e
            
            except KafkaError as e:
                logger.error(
                    "kafka_publish_error",
                    extra={
                        "attempt": attempt,
                        "error": str(e),
                        "will_retry": attempt < self.max_retries
                    }
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise EventBusError(f"Kafka publish failed: {e}") from e
    
    async def consume_events(
        self,
        topics: List[str],
        group_id: str,
        timeout_ms: int = 5000
    ) -> AsyncIterator[Dict[str, Any]]:
        """Consume events from Kafka topics."""
        try:
            if not self.consumer:
                self.consumer = KafkaConsumer(
                    *topics,
                    bootstrap_servers=self.bootstrap_servers,
                    group_id=group_id,
                    value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                    auto_offset_reset="earliest",
                    enable_auto_commit=True,
                    consumer_timeout_ms=timeout_ms
                )
            
            logger.info(
                "kafka_consume_start",
                extra={"topics": topics, "group_id": group_id}
            )
            
            for message in self.consumer:
                logger.debug(
                    "kafka_message_received",
                    extra={
                        "topic": message.topic,
                        "partition": message.partition,
                        "offset": message.offset
                    }
                )
                yield message.value
        
        except KafkaError as e:
            logger.error(
                "kafka_consume_error",
                extra={"error": str(e), "topics": topics}
            )
            raise EventBusError(f"Kafka consume failed: {e}") from e
    
    def _initialize_producer(self) -> None:
        """Initialize Kafka producer with timeout settings."""
        self.producer = KafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            request_timeout_ms=self.timeout_seconds * 1000,
            retries=self.max_retries,
            acks="all"  # Wait for all replicas to acknowledge
        )
    
    async def close(self) -> None:
        """Close Kafka connections."""
        if self.producer:
            self.producer.close()
            logger.info("kafka_producer_closed")
        
        if self.consumer:
            self.consumer.close()
            logger.info("kafka_consumer_closed")
```

---

### StoragePort: Local MinIO Adapter

```python
# src/adapters/storage/local.py
from typing import List, Dict, Any
from minio import Minio
from minio.error import S3Error
from loguru import logger
import asyncio
from concurrent.futures import ThreadPoolExecutor

from .port import StoragePort
from src.domain.exceptions import StorageError


class MinioStorage(StoragePort):
    """Local MinIO adapter using minio Python SDK."""
    
    def __init__(
        self,
        endpoint: str = "localhost:9000",
        access_key: str = "minioadmin",
        secret_key: str = "minioadmin",
        secure: bool = False,
        timeout_seconds: int = 30,
        max_retries: int = 3
    ):
        self.client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure
        )
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.executor = ThreadPoolExecutor(max_workers=5)
    
    async def put_object(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream"
    ) -> str:
        """Upload object to MinIO with retry logic."""
        from io import BytesIO
        
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(
                    "minio_put_start",
                    extra={
                        "bucket": bucket,
                        "key": key,
                        "size_bytes": len(data),
                        "attempt": attempt
                    }
                )
                
                # Run blocking I/O in thread pool
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    self.executor,
                    self.client.put_object,
                    bucket,
                    key,
                    BytesIO(data),
                    len(data),
                    content_type
                )
                
                object_url = f"http://localhost:9000/{bucket}/{key}"
                
                logger.info(
                    "minio_put_success",
                    extra={"url": object_url, "size_bytes": len(data)}
                )
                
                return object_url
            
            except S3Error as e:
                logger.error(
                    "minio_put_error",
                    extra={
                        "attempt": attempt,
                        "error": str(e),
                        "will_retry": attempt < self.max_retries
                    }
                )
                
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise StorageError(f"MinIO upload failed: {e}") from e
    
    async def get_object(self, bucket: str, key: str) -> bytes:
        """Download object from MinIO."""
        try:
            logger.info(
                "minio_get_start",
                extra={"bucket": bucket, "key": key}
            )
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                self.executor,
                self.client.get_object,
                bucket,
                key
            )
            
            data = response.read()
            
            logger.info(
                "minio_get_success",
                extra={"size_bytes": len(data)}
            )
            
            return data
        
        except S3Error as e:
            logger.error(
                "minio_get_error",
                extra={"bucket": bucket, "key": key, "error": str(e)}
            )
            raise StorageError(f"MinIO download failed: {e}") from e
    
    async def list_objects(self, bucket: str, prefix: str = "") -> List[str]:
        """List objects in bucket."""
        try:
            logger.info(
                "minio_list_start",
                extra={"bucket": bucket, "prefix": prefix}
            )
            
            loop = asyncio.get_event_loop()
            objects = await loop.run_in_executor(
                self.executor,
                self._list_objects_sync,
                bucket,
                prefix
            )
            
            logger.info(
                "minio_list_success",
                extra={"count": len(objects)}
            )
            
            return objects
        
        except S3Error as e:
            logger.error(
                "minio_list_error",
                extra={"bucket": bucket, "error": str(e)}
            )
            raise StorageError(f"MinIO list failed: {e}") from e
    
    def _list_objects_sync(self, bucket: str, prefix: str) -> List[str]:
        """Helper: sync list objects."""
        return [obj.object_name for obj in self.client.list_objects(bucket, prefix)]
    
    async def delete_object(self, bucket: str, key: str) -> None:
        """Delete object from MinIO."""
        try:
            logger.info(
                "minio_delete_start",
                extra={"bucket": bucket, "key": key}
            )
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self.executor,
                self.client.remove_object,
                bucket,
                key
            )
            
            logger.info("minio_delete_success", extra={"key": key})
        
        except S3Error as e:
            logger.error(
                "minio_delete_error",
                extra={"bucket": bucket, "key": key, "error": str(e)}
            )
            raise StorageError(f"MinIO delete failed: {e}") from e
```

---

### QueryPort: Local Trino Adapter

```python
# src/adapters/query/local.py
from typing import List, Dict, Any
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from loguru import logger
import asyncio
from concurrent.futures import ThreadPoolExecutor

from .port import QueryPort
from src.domain.exceptions import RepositoryError


class TrinoQuery(QueryPort):
    """Local Trino adapter using SQLAlchemy + trino connector."""
    
    def __init__(
        self,
        host: str = "localhost",
        port: int = 8080,
        user: str = "admin",
        catalog: str = "hive",
        schema: str = "default",
        timeout_seconds: int = 30
    ):
        # Trino connection string
        connection_url = f"trino://{user}@{host}:{port}/{catalog}/{schema}"
        
        self.engine = create_engine(
            connection_url,
            connect_args={
                "timeout_seconds": timeout_seconds,
                "verify": False
            },
            echo=False
        )
        self.timeout_seconds = timeout_seconds
        self.executor = ThreadPoolExecutor(max_workers=5)
    
    async def execute_query(
        self,
        sql: str,
        parameters: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        """Execute SQL query on Trino."""
        try:
            logger.info(
                "trino_execute_start",
                extra={
                    "sql_preview": sql[:100],
                    "parameters": parameters or {}
                }
            )
            
            # Run blocking I/O in thread pool
            loop = asyncio.get_event_loop()
            rows = await loop.run_in_executor(
                self.executor,
                self._execute_sync,
                sql,
                parameters
            )
            
            logger.info(
                "trino_execute_success",
                extra={"row_count": len(rows)}
            )
            
            return rows
        
        except SQLAlchemyError as e:
            logger.error(
                "trino_execute_error",
                extra={"error": str(e), "sql_preview": sql[:100]}
            )
            raise RepositoryError(f"Trino query failed: {e}") from e
    
    def _execute_sync(self, sql: str, parameters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Helper: sync query execution."""
        with self.engine.connect() as conn:
            stmt = text(sql)
            result = conn.execute(stmt, parameters or {})
            return [dict(row._mapping) for row in result]
    
    async def execute_query_streaming(
        self,
        sql: str,
        parameters: Dict[str, Any] = None
    ):
        """Execute query with streaming result."""
        try:
            logger.info(
                "trino_execute_streaming_start",
                extra={"sql_preview": sql[:100]}
            )
            
            with self.engine.connect() as conn:
                stmt = text(sql)
                result = conn.execute(stmt, parameters or {})
                
                for row in result:
                    yield dict(row._mapping)
        
        except SQLAlchemyError as e:
            logger.error(
                "trino_execute_streaming_error",
                extra={"error": str(e)}
            )
            raise RepositoryError(f"Trino streaming failed: {e}") from e
```

---

## AWS Adapters (Production)

### EventBusPort: AWS MSK Adapter

```python
# src/adapters/event_bus/aws.py
import json
from typing import List, Dict, Any, AsyncIterator
from kafka import KafkaProducer, KafkaConsumer
from kafka.errors import KafkaError, KafkaTimeoutError
from loguru import logger
import asyncio

from .port import EventBusPort
from src.domain.exceptions import EventBusError


class MskEventBus(EventBusPort):
    """AWS Managed Streaming for Kafka (MSK) adapter."""
    
    def __init__(
        self,
        bootstrap_servers: str,  # e.g., "kafka-1.msk.amazonaws.com:9092,kafka-2.msk.amazonaws.com:9092"
        timeout_seconds: int = 30,
        max_retries: int = 3,
        security_protocol: str = "SSL"
    ):
        self.bootstrap_servers = bootstrap_servers
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.security_protocol = security_protocol
        
        self.producer: KafkaProducer = None
        self.consumer: KafkaConsumer = None
    
    async def publish_event(self, topic: str, key: str, value: Dict[str, Any]) -> None:
        """Publish event to MSK topic."""
        if not self.producer:
            self._initialize_producer()
        
        message_bytes = json.dumps(value).encode("utf-8")
        
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(
                    "msk_publish_start",
                    extra={
                        "topic": topic,
                        "key": key,
                        "attempt": attempt
                    }
                )
                
                future = self.producer.send(
                    topic,
                    key=key.encode("utf-8"),
                    value=message_bytes
                )
                
                record_metadata = future.get(timeout=self.timeout_seconds)
                
                logger.info(
                    "msk_publish_success",
                    extra={
                        "topic": record_metadata.topic,
                        "partition": record_metadata.partition
                    }
                )
                return
            
            except KafkaTimeoutError as e:
                logger.warning(
                    "msk_publish_timeout",
                    extra={
                        "attempt": attempt,
                        "will_retry": attempt < self.max_retries
                    }
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise EventBusError(f"MSK publish timeout after {self.max_retries} retries") from e
            
            except KafkaError as e:
                logger.error(
                    "msk_publish_error",
                    extra={
                        "attempt": attempt,
                        "error": str(e),
                        "will_retry": attempt < self.max_retries
                    }
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise EventBusError(f"MSK publish failed: {e}") from e
    
    async def consume_events(
        self,
        topics: List[str],
        group_id: str,
        timeout_ms: int = 5000
    ) -> AsyncIterator[Dict[str, Any]]:
        """Consume events from MSK topics."""
        try:
            if not self.consumer:
                self.consumer = KafkaConsumer(
                    *topics,
                    bootstrap_servers=self.bootstrap_servers,
                    group_id=group_id,
                    value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                    security_protocol=self.security_protocol,
                    auto_offset_reset="latest",
                    enable_auto_commit=True,
                    consumer_timeout_ms=timeout_ms
                )
            
            logger.info(
                "msk_consume_start",
                extra={"topics": topics, "group_id": group_id}
            )
            
            for message in self.consumer:
                yield message.value
        
        except KafkaError as e:
            logger.error(
                "msk_consume_error",
                extra={"error": str(e), "topics": topics}
            )
            raise EventBusError(f"MSK consume failed: {e}") from e
    
    def _initialize_producer(self) -> None:
        """Initialize MSK producer with SSL settings."""
        self.producer = KafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            security_protocol=self.security_protocol,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            request_timeout_ms=self.timeout_seconds * 1000,
            retries=self.max_retries,
            acks="all"
        )
    
    async def close(self) -> None:
        """Close MSK connections."""
        if self.producer:
            self.producer.close()
            logger.info("msk_producer_closed")
        
        if self.consumer:
            self.consumer.close()
            logger.info("msk_consumer_closed")
```

---

### StoragePort: AWS S3 Adapter

```python
# src/adapters/storage/aws.py
from typing import List
import boto3
from botocore.exceptions import ClientError
from loguru import logger
import asyncio
from concurrent.futures import ThreadPoolExecutor

from .port import StoragePort
from src.domain.exceptions import StorageError


class S3Storage(StoragePort):
    """AWS S3 adapter using boto3."""
    
    def __init__(
        self,
        region_name: str = "us-east-1",
        timeout_seconds: int = 30,
        max_retries: int = 3
    ):
        self.s3_client = boto3.client("s3", region_name=region_name)
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.executor = ThreadPoolExecutor(max_workers=5)
    
    async def put_object(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream"
    ) -> str:
        """Upload object to S3."""
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(
                    "s3_put_start",
                    extra={
                        "bucket": bucket,
                        "key": key,
                        "size_bytes": len(data),
                        "attempt": attempt
                    }
                )
                
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    self.executor,
                    self.s3_client.put_object,
                    bucket,
                    key,
                    data,
                    None,  # ServerSideEncryption
                    None,  # StorageClass
                    content_type
                )
                
                object_url = f"s3://{bucket}/{key}"
                
                logger.info(
                    "s3_put_success",
                    extra={"url": object_url, "size_bytes": len(data)}
                )
                
                return object_url
            
            except ClientError as e:
                logger.error(
                    "s3_put_error",
                    extra={
                        "attempt": attempt,
                        "error": str(e),
                        "will_retry": attempt < self.max_retries
                    }
                )
                
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise StorageError(f"S3 upload failed: {e}") from e
    
    async def get_object(self, bucket: str, key: str) -> bytes:
        """Download object from S3."""
        try:
            logger.info(
                "s3_get_start",
                extra={"bucket": bucket, "key": key}
            )
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                self.executor,
                self.s3_client.get_object,
                bucket,
                key
            )
            
            data = response["Body"].read()
            
            logger.info(
                "s3_get_success",
                extra={"size_bytes": len(data)}
            )
            
            return data
        
        except ClientError as e:
            logger.error(
                "s3_get_error",
                extra={"bucket": bucket, "key": key, "error": str(e)}
            )
            raise StorageError(f"S3 download failed: {e}") from e
    
    async def list_objects(self, bucket: str, prefix: str = "") -> List[str]:
        """List objects in S3 bucket."""
        try:
            logger.info(
                "s3_list_start",
                extra={"bucket": bucket, "prefix": prefix}
            )
            
            loop = asyncio.get_event_loop()
            objects = await loop.run_in_executor(
                self.executor,
                self._list_objects_sync,
                bucket,
                prefix
            )
            
            logger.info(
                "s3_list_success",
                extra={"count": len(objects)}
            )
            
            return objects
        
        except ClientError as e:
            logger.error(
                "s3_list_error",
                extra={"bucket": bucket, "error": str(e)}
            )
            raise StorageError(f"S3 list failed: {e}") from e
    
    def _list_objects_sync(self, bucket: str, prefix: str) -> List[str]:
        """Helper: sync list objects."""
        paginator = self.s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
        
        keys = []
        for page in pages:
            if "Contents" in page:
                keys.extend([obj["Key"] for obj in page["Contents"]])
        
        return keys
    
    async def delete_object(self, bucket: str, key: str) -> None:
        """Delete object from S3."""
        try:
            logger.info(
                "s3_delete_start",
                extra={"bucket": bucket, "key": key}
            )
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self.executor,
                self.s3_client.delete_object,
                bucket,
                key
            )
            
            logger.info("s3_delete_success", extra={"key": key})
        
        except ClientError as e:
            logger.error(
                "s3_delete_error",
                extra={"bucket": bucket, "key": key, "error": str(e)}
            )
            raise StorageError(f"S3 delete failed: {e}") from e
```

---

### QueryPort: AWS Athena Adapter

```python
# src/adapters/query/aws.py
from typing import List, Dict, Any
import boto3
from botocore.exceptions import ClientError
from loguru import logger
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

from .port import QueryPort
from src.domain.exceptions import RepositoryError


class AthenaQuery(QueryPort):
    """AWS Athena adapter using boto3."""
    
    def __init__(
        self,
        s3_output_location: str,  # e.g., "s3://my-bucket/athena-results/"
        region_name: str = "us-east-1",
        database: str = "default",
        timeout_seconds: int = 300,
        max_retries: int = 3
    ):
        self.athena_client = boto3.client("athena", region_name=region_name)
        self.s3_output_location = s3_output_location
        self.database = database
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.executor = ThreadPoolExecutor(max_workers=5)
    
    async def execute_query(
        self,
        sql: str,
        parameters: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        """Execute SQL query on Athena."""
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(
                    "athena_execute_start",
                    extra={
                        "sql_preview": sql[:100],
                        "attempt": attempt
                    }
                )
                
                # Substitute parameters into SQL (Athena doesn't support parameterized queries)
                query_sql = sql
                if parameters:
                    for key, value in parameters.items():
                        query_sql = query_sql.replace(f":{key}", f"'{value}'")
                
                loop = asyncio.get_event_loop()
                rows = await loop.run_in_executor(
                    self.executor,
                    self._execute_sync,
                    query_sql
                )
                
                logger.info(
                    "athena_execute_success",
                    extra={"row_count": len(rows)}
                )
                
                return rows
            
            except ClientError as e:
                logger.error(
                    "athena_execute_error",
                    extra={
                        "attempt": attempt,
                        "error": str(e),
                        "will_retry": attempt < self.max_retries
                    }
                )
                
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise RepositoryError(f"Athena query failed: {e}") from e
    
    def _execute_sync(self, sql: str) -> List[Dict[str, Any]]:
        """Helper: sync Athena query execution."""
        # Start query execution
        response = self.athena_client.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": self.database},
            ResultConfiguration={"OutputLocation": self.s3_output_location}
        )
        
        query_execution_id = response["QueryExecutionId"]
        
        # Wait for query to complete
        max_attempts = int(self.timeout_seconds / 2)
        for attempt in range(max_attempts):
            query_status = self.athena_client.get_query_execution(
                QueryExecutionId=query_execution_id
            )
            
            status = query_status["QueryExecution"]["Status"]["State"]
            
            if status == "SUCCEEDED":
                break
            elif status == "FAILED":
                reason = query_status["QueryExecution"]["Status"].get("StateChangeReason", "Unknown")
                raise Exception(f"Athena query failed: {reason}")
            elif status == "CANCELLED":
                raise Exception("Athena query was cancelled")
            
            time.sleep(2)
        else:
            raise Exception(f"Athena query timeout after {self.timeout_seconds}s")
        
        # Fetch results
        result_rows = self.athena_client.get_query_results(
            QueryExecutionId=query_execution_id,
            MaxResults=1000
        )
        
        # Parse results
        rows = []
        result_data = result_rows["ResultSet"]["Rows"]
        
        if not result_data:
            return rows
        
        # First row is column names
        columns = [col["VarCharValue"] for col in result_data[0]["Data"]]
        
        # Remaining rows are data
        for row in result_data[1:]:
            row_dict = {}
            for i, col_name in enumerate(columns):
                value = row["Data"][i].get("VarCharValue")
                row_dict[col_name] = value
            rows.append(row_dict)
        
        return rows
    
    async def execute_query_streaming(
        self,
        sql: str,
        parameters: Dict[str, Any] = None
    ):
        """Execute query with streaming result."""
        # For Athena, fetch all results then yield
        results = await self.execute_query(sql, parameters)
        for row in results:
            yield row
```

---

## Adapter Factory (CloudProvider Pattern)

```python
# src/adapters/__init__.py
import os
from enum import Enum
from typing import Literal

from .event_bus.port import EventBusPort
from .storage.port import StoragePort
from .query.port import QueryPort
from .dbt.port import DbtPort


class CloudProvider(Enum):
    LOCAL = "local"
    AWS = "aws"


def get_cloud_provider() -> CloudProvider:
    """Determine cloud provider from environment."""
    profile = os.getenv("RIDESTREAM_PROFILE", "dev")
    
    if profile == "dev":
        return CloudProvider.LOCAL
    elif profile in ("staging", "prod"):
        return CloudProvider.AWS
    else:
        raise ValueError(f"Unknown profile: {profile}")


def get_event_bus() -> EventBusPort:
    """Factory: returns appropriate EventBus adapter."""
    provider = get_cloud_provider()
    
    if provider == CloudProvider.LOCAL:
        from .event_bus.local import KafkaEventBus
        return KafkaEventBus(
            bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        )
    else:
        from .event_bus.aws import MskEventBus
        return MskEventBus(
            bootstrap_servers=os.getenv("MSK_BOOTSTRAP_SERVERS")
        )


def get_storage() -> StoragePort:
    """Factory: returns appropriate Storage adapter."""
    provider = get_cloud_provider()
    
    if provider == CloudProvider.LOCAL:
        from .storage.local import MinioStorage
        return MinioStorage(
            endpoint=os.getenv("MINIO_ENDPOINT", "localhost:9000"),
            access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
            secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin")
        )
    else:
        from .storage.aws import S3Storage
        return S3Storage(
            region_name=os.getenv("AWS_REGION", "us-east-1")
        )


def get_query() -> QueryPort:
    """Factory: returns appropriate Query adapter."""
    provider = get_cloud_provider()
    
    if provider == CloudProvider.LOCAL:
        from .query.local import TrinoQuery
        return TrinoQuery(
            host=os.getenv("TRINO_HOST", "localhost"),
            port=int(os.getenv("TRINO_PORT", "8080")),
            catalog=os.getenv("TRINO_CATALOG", "hive"),
            schema=os.getenv("TRINO_SCHEMA", "default")
        )
    else:
        from .query.aws import AthenaQuery
        return AthenaQuery(
            s3_output_location=os.getenv("ATHENA_S3_OUTPUT"),
            database=os.getenv("ATHENA_DATABASE", "ridestream_prod")
        )


def get_dbt() -> DbtPort:
    """Factory: returns appropriate dbt adapter."""
    provider = get_cloud_provider()
    
    if provider == CloudProvider.LOCAL:
        from .dbt.local import DbtTrinoAdapter
        return DbtTrinoAdapter()
    else:
        from .dbt.aws import DbtAthenaAdapter
        return DbtAthenaAdapter()
```

---

## Error Mapping

```python
# src/domain/exceptions.py
class DomainError(Exception):
    """Base domain exception."""
    pass


class EventBusError(DomainError):
    """Raised when event publishing/consuming fails."""
    pass


class StorageError(DomainError):
    """Raised when object storage operation fails."""
    pass


class RepositoryError(DomainError):
    """Raised when query execution fails."""
    pass


class DbtRunFailedError(DomainError):
    """Raised when dbt model execution fails."""
    pass
```

Error mapping examples (in each adapter):
- `kafka.errors.KafkaError` → `EventBusError`
- `kafka.errors.KafkaTimeoutError` → `EventBusError`
- `minio.error.S3Error` → `StorageError`
- `botocore.exceptions.ClientError` → `StorageError` (S3) or `RepositoryError` (Athena)
- `sqlalchemy.exc.SQLAlchemyError` → `RepositoryError`

Never allow infrastructure exceptions to leak to domain/application layer.

---

## Connection Management

### Kafka (Event Bus)
- Producers/consumers initialized on demand
- Thread-safe (kafka-python handles this)
- Closed via `await adapter.close()` on shutdown

### MinIO/S3 (Storage)
- Clients created on init
- Thread pool executor for blocking I/O
- Max 5 concurrent operations

### Trino/Athena (Query)
- Connection pooling via SQLAlchemy (Trino) or connection reuse (Athena)
- Thread pool for blocking calls
- Query execution with timeout

All adapters must support graceful shutdown via context managers or explicit `close()` methods.

---

## Testing Adapters

```python
# tests/unit/adapters/test_event_bus_local.py
import pytest
from src.adapters.event_bus.local import KafkaEventBus
from src.domain.exceptions import EventBusError

@pytest.fixture
async def kafka_bus():
    bus = KafkaEventBus(bootstrap_servers="localhost:9092")
    yield bus
    await bus.close()

@pytest.mark.asyncio
async def test_publish_event(kafka_bus):
    """Unit test: publish to Kafka."""
    await kafka_bus.publish_event(
        topic="rides",
        key="ride_123",
        value={"driver_id": "driver_1", "status": "pending"}
    )

# tests/integration/adapters/test_event_bus_msk.py (requires AWS credentials)
@pytest.mark.asyncio
async def test_publish_event_msk(aws_credentials):
    """Integration test: publish to MSK."""
    bus = MskEventBus(bootstrap_servers=os.getenv("MSK_BOOTSTRAP_SERVERS"))
    
    await bus.publish_event(
        topic="rides",
        key="ride_123",
        value={"driver_id": "driver_1"}
    )
    
    await bus.close()
```

---

## Secrets Management

**Local Development**: Environment variables in `.env`
```bash
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
TRINO_HOST=localhost
TRINO_PORT=8080
```

**Production (AWS)**:
```python
import boto3
import json

def get_secrets(secret_id: str) -> dict:
    client = boto3.client("secretsmanager", region_name="us-east-1")
    secret = client.get_secret_value(SecretId=secret_id)
    return json.loads(secret["SecretString"])

# Usage
msk_creds = get_secrets("prod/ridestream/msk")
athena_config = get_secrets("prod/ridestream/athena")
```

---

## Adapter Structure

```
src/adapters/
├── __init__.py                # Factories (get_event_bus, get_storage, etc.)
├── ports/
│   ├── event_bus.py          # EventBusPort
│   ├── storage.py            # StoragePort
│   ├── query.py              # QueryPort
│   └── dbt.py                # DbtPort
├── event_bus/
│   ├── local.py              # KafkaEventBus
│   └── aws.py                # MskEventBus
├── storage/
│   ├── local.py              # MinioStorage
│   └── aws.py                # S3Storage
├── query/
│   ├── local.py              # TrinoQuery
│   └── aws.py                # AthenaQuery
└── dbt/
    ├── local.py              # DbtTrinoAdapter
    └── aws.py                # DbtAthenaAdapter
```

---

## Anti-Patterns (Never Do This)

- **Hardcoded credentials** in adapter code
- **Bare `except Exception`** — always catch specific errors and map to domain exceptions
- **Leaking infrastructure errors** to caller — wrap all external exceptions
- **Blocking I/O without executor** — use thread pool for sync operations
- **No timeout** on network calls — always set timeout_seconds
- **No retry logic** on transient failures — implement exponential backoff
- **Mixing local/AWS logic** in single adapter — keep completely separate implementations
- **No structured logging** — log request, response, errors with context
- **Single-target adapters** — every port MUST have local + AWS simultaneously
- **Adapter imports in application layer** — application imports only ports
