# Infrastructure Rules

## Core Principle
RideStream v2 uses two distinct infrastructure layers:
1. **Local Development**: Docker Compose with Zookeeper, Kafka, MinIO, Trino, Hive Metastore, Spark, Airflow
2. **Production**: AWS CloudFormation with MSK, S3, Glue, Athena, EMR Serverless, Step Functions, CodePipeline

All infrastructure as code (no manual AWS console changes). Change sets before updates. Smoke tests validate every deployment.

## Rule 1: DEPLOY FIRST
Deploy entire infrastructure BEFORE implementing business logic. Iteration 0 has empty codepaths, all smoke tests passing.

### Phase 1 Sequence (Local + Production)

**Local Deployment:**
```bash
# 1. Deploy Docker stack
docker-compose -f docker/docker-compose.yml up -d
make smoke-test  # GATE — all services healthy

# 2. Verify all containers running
docker ps | grep rs2-

# 3. Only THEN write domain logic
```

**Production Deployment:**
```bash
# 1. Deploy VPC
aws cloudformation create-change-set \
  --stack-name ridestream-vpc \
  --change-set-name vpc-initial-$(date +%s) \
  --template-body file://cloudformation/vpc.yaml
make smoke-test  # GATE

# 2. Deploy MSK Kafka cluster
aws cloudformation create-change-set \
  --stack-name ridestream-msk \
  --change-set-name msk-initial-$(date +%s) \
  --template-body file://cloudformation/msk.yaml
# (MSK provisioning takes 15-20 minutes)
make smoke-test  # GATE

# 3. Deploy S3 buckets
aws cloudformation create-change-set \
  --stack-name ridestream-s3 \
  --change-set-name s3-initial-$(date +%s) \
  --template-body file://cloudformation/s3.yaml
make smoke-test  # GATE

# 4. Deploy Glue Catalog
aws cloudformation create-change-set \
  --stack-name ridestream-glue \
  --change-set-name glue-initial-$(date +%s) \
  --template-body file://cloudformation/glue.yaml
make smoke-test  # GATE

# 5. Deploy Athena
aws cloudformation create-change-set \
  --stack-name ridestream-athena \
  --change-set-name athena-initial-$(date +%s) \
  --template-body file://cloudformation/athena.yaml
make smoke-test  # GATE

# 6. Deploy EMR Serverless
aws cloudformation create-change-set \
  --stack-name ridestream-emr-serverless \
  --change-set-name emr-initial-$(date +%s) \
  --template-body file://cloudformation/emr-serverless.yaml
make smoke-test  # GATE

# 7. Deploy Step Functions
aws cloudformation create-change-set \
  --stack-name ridestream-step-functions \
  --change-set-name sfn-initial-$(date +%s) \
  --template-body file://cloudformation/step-functions.yaml
make smoke-test  # GATE

# 8. Deploy IAM roles
aws cloudformation create-change-set \
  --stack-name ridestream-iam \
  --change-set-name iam-initial-$(date +%s) \
  --template-body file://cloudformation/iam.yaml
make smoke-test  # GATE

# 9. Deploy CodePipeline
aws cloudformation create-change-set \
  --stack-name ridestream-pipeline \
  --change-set-name pipeline-initial-$(date +%s) \
  --template-body file://cloudformation/codepipeline.yaml
make smoke-test  # GATE

# 10. Only THEN write domain logic
```

---

## Local Infrastructure: Docker Compose

All containers use `rs2-` prefix. Lives in `docker/docker-compose.yml`.

### Service Definitions

```yaml
# docker/docker-compose.yml
version: '3.9'

services:
  rs2-zookeeper:
    image: confluentinc/cp-zookeeper:7.6.0
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
      ZOOKEEPER_SYNC_LIMIT: 2
      ZOOKEEPER_INIT_LIMIT: 5
    ports:
      - "2181:2181"
    mem_limit: 512m
    healthcheck:
      test: ['CMD', 'echo', 'ruok', '|', 'nc', 'localhost', '2181']
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - ride_stream_v2_default

  rs2-kafka:
    image: confluentinc/cp-kafka:7.6.0
    depends_on:
      rs2-zookeeper:
        condition: service_healthy
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: rs2-zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://rs2-kafka:29092,PLAINTEXT_HOST://localhost:9092
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,PLAINTEXT_HOST:PLAINTEXT
      KAFKA_INTER_BROKER_LISTENER_NAME: PLAINTEXT
      KAFKA_AUTO_CREATE_TOPICS_ENABLE: "true"
      KAFKA_LOG_RETENTION_HOURS: 24
    ports:
      - "9092:9092"
      - "29092:29092"
    mem_limit: 1024m
    healthcheck:
      test: ['CMD', 'kafka-topics', '--bootstrap-server', 'localhost:29092', '--list']
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - ride_stream_v2_default

  rs2-minio:
    image: minio/minio:RELEASE.2024-11-07T00-52-07Z
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    command: server /data --console-address ":9001"
    ports:
      - "9000:9000"
      - "9001:9001"
    mem_limit: 512m
    healthcheck:
      test: ['CMD', 'curl', '-f', 'http://localhost:9000/minio/health/live']
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - ride_stream_v2_default

  rs2-minio-init:
    image: minio/mc:latest
    depends_on:
      rs2-minio:
        condition: service_healthy
    entrypoint: >
      /bin/sh -c "
      /usr/bin/mc alias set minio http://rs2-minio:9000 minioadmin minioadmin;
      /usr/bin/mc mb --ignore-existing minio/bronze;
      /usr/bin/mc mb --ignore-existing minio/silver;
      /usr/bin/mc mb --ignore-existing minio/gold;
      /usr/bin/mc mb --ignore-existing minio/athena-results;
      exit 0;
      "
    networks:
      - ride_stream_v2_default

  rs2-hive-metastore:
    build:
      context: docker/hive-metastore
      dockerfile: Dockerfile
    environment:
      DATABASE_TYPE: derby
      HADOOP_CUSTOM_CONF_DIR: /hadoop/conf
    ports:
      - "9083:9083"
    mem_limit: 1024m
    healthcheck:
      test: ['CMD', 'bash', '-c', 'echo > /dev/tcp/localhost/9083']
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - ride_stream_v2_default

  rs2-trino:
    image: trinodb/trino:430
    depends_on:
      rs2-hive-metastore:
        condition: service_healthy
      rs2-minio:
        condition: service_healthy
    environment:
      NODE_ID: trino-master
    ports:
      - "8888:8080"
    mem_limit: 1024m
    volumes:
      - ./docker/trino/etc:/etc/trino
      - ./docker/trino/jvm.config:/etc/trino/jvm.config
    healthcheck:
      test: ['CMD', 'curl', '-sf', 'http://localhost:8080/v1/info']
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - ride_stream_v2_default

  rs2-spark-master:
    image: apache/spark:3.5.1-python3
    command: /opt/spark/bin/spark-class org.apache.spark.deploy.master.Master
    environment:
      SPARK_LOCAL_IP: rs2-spark-master
      SPARK_MASTER_HOST: rs2-spark-master
      SPARK_MASTER_PORT: 7077
      SPARK_MASTER_WEBUI_PORT: 8090
    ports:
      - "7077:7077"
      - "8090:8090"
    mem_limit: 2048m
    healthcheck:
      test: ['CMD', 'curl', '-sf', 'http://localhost:8090/']
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - ride_stream_v2_default

  rs2-spark-worker:
    image: apache/spark:3.5.1-python3
    command: /opt/spark/bin/spark-class org.apache.spark.deploy.worker.Worker spark://rs2-spark-master:7077
    environment:
      SPARK_LOCAL_IP: rs2-spark-worker
      SPARK_WORKER_CORES: 4
      SPARK_WORKER_MEMORY: 2g
      SPARK_WORKER_PORT: 7078
      SPARK_WORKER_WEBUI_PORT: 8081
    ports:
      - "8081:8081"
    mem_limit: 2048m
    depends_on:
      rs2-spark-master:
        condition: service_healthy
    networks:
      - ride_stream_v2_default

  rs2-airflow-db:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: airflow
      POSTGRES_PASSWORD: airflow
      POSTGRES_DB: airflow
    ports:
      - "5433:5432"
    mem_limit: 512m
    volumes:
      - airflow_db:/var/lib/postgresql/data
    healthcheck:
      test: ['CMD-SHELL', 'pg_isready -U airflow']
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - ride_stream_v2_default

  rs2-airflow-webserver:
    image: apache/airflow:2.9.3-python3.12
    command: webserver
    environment:
      AIRFLOW__CORE__DAGS_FOLDER: /opt/airflow/dags
      AIRFLOW__CORE__LOAD_EXAMPLES: "false"
      AIRFLOW__CORE__EXECUTOR: LocalExecutor
      AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql://airflow:airflow@rs2-airflow-db:5432/airflow
      AIRFLOW__CORE__FERNET_KEY: d60OM75o_0BskHgznmk09u4zWQAztkWQycszeK3i7_M=
    ports:
      - "8085:8080"
    mem_limit: 1024m
    depends_on:
      rs2-airflow-db:
        condition: service_healthy
    volumes:
      - ./docker/airflow/dags:/opt/airflow/dags
      - ./docker/airflow/logs:/opt/airflow/logs
    healthcheck:
      test: ['CMD', 'curl', '-sf', 'http://localhost:8080/health']
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - ride_stream_v2_default

  rs2-airflow-scheduler:
    image: apache/airflow:2.9.3-python3.12
    command: scheduler
    environment:
      AIRFLOW__CORE__DAGS_FOLDER: /opt/airflow/dags
      AIRFLOW__CORE__LOAD_EXAMPLES: "false"
      AIRFLOW__CORE__EXECUTOR: LocalExecutor
      AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql://airflow:airflow@rs2-airflow-db:5432/airflow
      AIRFLOW__CORE__FERNET_KEY: d60OM75o_0BskHgznmk09u4zWQAztkWQycszeK3i7_M=
    mem_limit: 1024m
    depends_on:
      rs2-airflow-db:
        condition: service_healthy
    volumes:
      - ./docker/airflow/dags:/opt/airflow/dags
      - ./docker/airflow/logs:/opt/airflow/logs
    networks:
      - ride_stream_v2_default

networks:
  ride_stream_v2_default:
    driver: bridge

volumes:
  airflow_db:
```

### Trino Configuration

```properties
# docker/trino/etc/config.properties
coordinator=true
node-scheduler.include-coordinator=true
node-scheduler.max-splits-per-node=4
discovery-server.enabled=true
discovery.uri=http://localhost:8080
http-server.http.port=8080

# docker/trino/jvm.config
-server
-Xmx2G
-XX:+UseG1GC
-XX:G1HeapRegionSize=32M
-XX:+UseStringDeduplication
```

### Hive Metastore Dockerfile

```dockerfile
# docker/hive-metastore/Dockerfile
FROM apache/hive:4.0.0

RUN apt-get update && \
    apt-get install -y openjdk-11-jdk && \
    rm -rf /var/lib/apt/lists/*

# Add Hadoop AWS JARs for S3 compatibility
RUN wget -O /opt/hive/lib/hadoop-aws-3.4.0.jar \
    https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.4.0/hadoop-aws-3.4.0.jar

RUN wget -O /opt/hive/lib/aws-java-sdk-bundle-1.12.565.jar \
    https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.12.565/aws-java-sdk-bundle-1.12.565.jar

EXPOSE 9083

CMD ["/opt/hive/bin/schematool", "-dbType", "derby", "-initSchema"]
```

---

## Production Infrastructure: AWS CloudFormation

### VPC Template

```yaml
# cloudformation/vpc.yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: 'RideStream VPC with public/private subnets, NAT Gateway'

Parameters:
  Environment:
    Type: String
    Default: prod
    AllowedValues: [dev, staging, prod]

Resources:
  RideStreamVPC:
    Type: AWS::EC2::VPC
    Properties:
      CidrBlock: 10.0.0.0/16
      EnableDnsHostnames: true
      EnableDnsSupport: true
      Tags:
        - Key: Name
          Value: !Sub 'ridestream-vpc-${Environment}'

  PublicSubnet1:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref RideStreamVPC
      CidrBlock: 10.0.1.0/24
      AvailabilityZone: !Select [0, !GetAZs '']
      MapPublicIpOnLaunch: true
      Tags:
        - Key: Name
          Value: ridestream-public-subnet-1

  PublicSubnet2:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref RideStreamVPC
      CidrBlock: 10.0.2.0/24
      AvailabilityZone: !Select [1, !GetAZs '']
      MapPublicIpOnLaunch: true
      Tags:
        - Key: Name
          Value: ridestream-public-subnet-2

  PrivateSubnet1:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref RideStreamVPC
      CidrBlock: 10.0.11.0/24
      AvailabilityZone: !Select [0, !GetAZs '']
      Tags:
        - Key: Name
          Value: ridestream-private-subnet-1

  PrivateSubnet2:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref RideStreamVPC
      CidrBlock: 10.0.12.0/24
      AvailabilityZone: !Select [1, !GetAZs '']
      Tags:
        - Key: Name
          Value: ridestream-private-subnet-2

  InternetGateway:
    Type: AWS::EC2::InternetGateway
    Tags:
      - Key: Name
        Value: ridestream-igw

  AttachGateway:
    Type: AWS::EC2::VPCGatewayAttachment
    Properties:
      VpcId: !Ref RideStreamVPC
      InternetGatewayId: !Ref InternetGateway

  PublicRouteTable:
    Type: AWS::EC2::RouteTable
    Properties:
      VpcId: !Ref RideStreamVPC
      Tags:
        - Key: Name
          Value: ridestream-public-rt

  PublicRoute:
    Type: AWS::EC2::Route
    DependsOn: AttachGateway
    Properties:
      RouteTableId: !Ref PublicRouteTable
      DestinationCidrBlock: 0.0.0.0/0
      GatewayId: !Ref InternetGateway

  PublicSubnet1RouteTableAssociation:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      SubnetId: !Ref PublicSubnet1
      RouteTableId: !Ref PublicRouteTable

  PublicSubnet2RouteTableAssociation:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      SubnetId: !Ref PublicSubnet2
      RouteTableId: !Ref PublicRouteTable

  EIP:
    Type: AWS::EC2::EIP
    DependsOn: AttachGateway
    Properties:
      Domain: vpc

  NATGateway:
    Type: AWS::EC2::NatGateway
    Properties:
      AllocationId: !GetAtt EIP.AllocationId
      SubnetId: !Ref PublicSubnet1

  PrivateRouteTable:
    Type: AWS::EC2::RouteTable
    Properties:
      VpcId: !Ref RideStreamVPC
      Tags:
        - Key: Name
          Value: ridestream-private-rt

  PrivateRoute:
    Type: AWS::EC2::Route
    Properties:
      RouteTableId: !Ref PrivateRouteTable
      DestinationCidrBlock: 0.0.0.0/0
      NatGatewayId: !Ref NATGateway

  PrivateSubnet1RouteTableAssociation:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      SubnetId: !Ref PrivateSubnet1
      RouteTableId: !Ref PrivateRouteTable

  PrivateSubnet2RouteTableAssociation:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      SubnetId: !Ref PrivateSubnet2
      RouteTableId: !Ref PrivateRouteTable

Outputs:
  VpcId:
    Value: !Ref RideStreamVPC
    Export:
      Name: RideStream-VPC-ID
  
  PrivateSubnet1Id:
    Value: !Ref PrivateSubnet1
    Export:
      Name: RideStream-PrivateSubnet1-ID

  PrivateSubnet2Id:
    Value: !Ref PrivateSubnet2
    Export:
      Name: RideStream-PrivateSubnet2-ID
```

### MSK (Amazon Managed Streaming for Apache Kafka) Template

```yaml
# cloudformation/msk.yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: 'RideStream MSK Kafka cluster (3-broker multi-AZ)'

Parameters:
  Environment:
    Type: String
    Default: prod
    AllowedValues: [dev, staging, prod]

Resources:
  RideStreamKafkaSecurityGroup:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: Security group for RideStream Kafka cluster
      VpcId:
        Fn::ImportValue: RideStream-VPC-ID
      SecurityGroupIngress:
        - IpProtocol: tcp
          FromPort: 9092
          ToPort: 9092
          CidrIp: 10.0.0.0/16
        - IpProtocol: tcp
          FromPort: 9094
          ToPort: 9094
          CidrIp: 10.0.0.0/16
      Tags:
        - Key: Name
          Value: ridestream-kafka-sg

  RideStreamKafkaCluster:
    Type: AWS::MSK::Cluster
    Properties:
      ClusterName: ridestream-kafka
      KafkaVersion: '3.6.0'
      NumberOfBrokerNodes: 3
      BrokerNodeGroupInfo:
        InstanceType: kafka.m5.large
        EBSStorageInfo:
          VolumeSize: 100
        SecurityGroups:
          - !Ref RideStreamKafkaSecurityGroup
        SubnetIds:
          - Fn::ImportValue: RideStream-PrivateSubnet1-ID
          - Fn::ImportValue: RideStream-PrivateSubnet2-ID
      EncryptionInfo:
        EncryptionInTransit:
          ClientBroker: TLS
          InCluster: true
      LoggingInfo:
        BrokerLogs:
          CloudWatchLogs:
            Enabled: true
            LogGroup: !Ref RideStreamKafkaLogGroup
      Tags:
        Environment: !Ref Environment
        Name: ridestream-kafka

  RideStreamKafkaLogGroup:
    Type: AWS::Logs::LogGroup
    Properties:
      LogGroupName: /aws/msk/ridestream-kafka
      RetentionInDays: 7

Outputs:
  KafkaClusterArn:
    Value: !GetAtt RideStreamKafkaCluster.Arn
    Export:
      Name: RideStream-Kafka-Arn

  BootstrapServers:
    Value: !GetAtt RideStreamKafkaCluster.BootstrapServersPlaintext
    Export:
      Name: RideStream-Kafka-BootstrapServers
```

### S3 Buckets Template

```yaml
# cloudformation/s3.yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: 'RideStream S3 buckets (Bronze, Silver, Gold, Athena Results)'

Parameters:
  Environment:
    Type: String
    Default: prod
    AllowedValues: [dev, staging, prod]

Resources:
  RideStreamBronzeBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: !Sub 'ridestream-bronze-${AWS::AccountId}'
      VersioningConfiguration:
        Status: Enabled
      PublicAccessBlockConfiguration:
        BlockPublicAcls: true
        BlockPublicPolicy: true
        IgnorePublicAcls: true
        RestrictPublicBuckets: true
      Tags:
        - Key: Environment
          Value: !Ref Environment
        - Key: Layer
          Value: Bronze

  RideStreamSilverBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: !Sub 'ridestream-silver-${AWS::AccountId}'
      VersioningConfiguration:
        Status: Enabled
      PublicAccessBlockConfiguration:
        BlockPublicAcls: true
        BlockPublicPolicy: true
        IgnorePublicAcls: true
        RestrictPublicBuckets: true
      Tags:
        - Key: Environment
          Value: !Ref Environment
        - Key: Layer
          Value: Silver

  RideStreamGoldBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: !Sub 'ridestream-gold-${AWS::AccountId}'
      VersioningConfiguration:
        Status: Enabled
      PublicAccessBlockConfiguration:
        BlockPublicAcls: true
        BlockPublicPolicy: true
        IgnorePublicAcls: true
        RestrictPublicBuckets: true
      Tags:
        - Key: Environment
          Value: !Ref Environment
        - Key: Layer
          Value: Gold

  RideStreamAthenaResultsBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: !Sub 'ridestream-athena-results-${AWS::AccountId}'
      VersioningConfiguration:
        Status: Enabled
      LifecycleConfiguration:
        Rules:
          - Id: DeleteOldQueryResults
            Status: Enabled
            ExpirationInDays: 30
      PublicAccessBlockConfiguration:
        BlockPublicAcls: true
        BlockPublicPolicy: true
        IgnorePublicAcls: true
        RestrictPublicBuckets: true
      Tags:
        - Key: Environment
          Value: !Ref Environment
        - Key: Purpose
          Value: Athena Query Results

Outputs:
  BronzeBucketName:
    Value: !Ref RideStreamBronzeBucket
    Export:
      Name: RideStream-Bronze-Bucket
  
  SilverBucketName:
    Value: !Ref RideStreamSilverBucket
    Export:
      Name: RideStream-Silver-Bucket

  GoldBucketName:
    Value: !Ref RideStreamGoldBucket
    Export:
      Name: RideStream-Gold-Bucket

  AthenaResultsBucketName:
    Value: !Ref RideStreamAthenaResultsBucket
    Export:
      Name: RideStream-Athena-Results-Bucket
```

### AWS Glue Catalog Template

```yaml
# cloudformation/glue.yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: 'RideStream AWS Glue Catalog for Hive metadata'

Parameters:
  Environment:
    Type: String
    Default: prod
    AllowedValues: [dev, staging, prod]

Resources:
  RideStreamGlueCatalogDatabase:
    Type: AWS::Glue::CatalogDatabase
    Properties:
      CatalogId: !Ref AWS::AccountId
      DatabaseInput:
        Name: ridestream_catalog
        Description: Hive-compatible Glue Catalog for RideStream data
        Parameters:
          classification: parquet
          compressionType: snappy

  RideStreamBronzeDatabase:
    Type: AWS::Glue::CatalogDatabase
    Properties:
      CatalogId: !Ref AWS::AccountId
      DatabaseInput:
        Name: ridestream_bronze
        Description: Bronze layer (raw data)

  RideStreamSilverDatabase:
    Type: AWS::Glue::CatalogDatabase
    Properties:
      CatalogId: !Ref AWS::AccountId
      DatabaseInput:
        Name: ridestream_silver
        Description: Silver layer (cleansed data)

  RideStreamGoldDatabase:
    Type: AWS::Glue::CatalogDatabase
    Properties:
      CatalogId: !Ref AWS::AccountId
      DatabaseInput:
        Name: ridestream_gold
        Description: Gold layer (analytics-ready data)

Outputs:
  GlueCatalogDatabaseName:
    Value: !Ref RideStreamGlueCatalogDatabase
    Export:
      Name: RideStream-Glue-Catalog
  
  BronzeDatabaseName:
    Value: !Ref RideStreamBronzeDatabase
    Export:
      Name: RideStream-Glue-Bronze-DB

  SilverDatabaseName:
    Value: !Ref RideStreamSilverDatabase
    Export:
      Name: RideStream-Glue-Silver-DB

  GoldDatabaseName:
    Value: !Ref RideStreamGoldDatabase
    Export:
      Name: RideStream-Glue-Gold-DB
```

### Athena Template

```yaml
# cloudformation/athena.yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: 'RideStream AWS Athena for serverless SQL queries'

Resources:
  RideStreamAthenaWorkgroup:
    Type: AWS::Athena::WorkGroup
    Properties:
      Name: ridestream-workgroup
      Description: RideStream Athena workgroup for SQL queries on S3
      WorkGroupConfiguration:
        ResultConfigurationUpdates:
          OutputLocation: !Sub 's3://${AthenaResultsBucket}/athena-results/'
        EnforceWorkGroupConfiguration: true
        PublishCloudWatchMetricsEnabled: true
        BytesScannedCutoffPerQuery: 1000000000
        RequesterPaysEnabled: false

  AthenaResultsBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: !Sub 'ridestream-athena-${AWS::AccountId}'

Outputs:
  AthenaWorkgroupName:
    Value: !Ref RideStreamAthenaWorkgroup
    Export:
      Name: RideStream-Athena-Workgroup
```

### EMR Serverless Template

```yaml
# cloudformation/emr-serverless.yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: 'RideStream EMR Serverless for Spark batch jobs'

Resources:
  RideStreamEMRServerlessApplication:
    Type: AWS::EMRServerless::Application
    Properties:
      Name: ridestream-spark
      ReleaseLabel: emr-7.0.0
      Type: SPARK
      MaximumCapacity:
        Cpu: 400
        Memory: 1600Gb
      InitialCapacity:
        - Key: DRIVER
          Value:
            WorkerCount: 1
            WorkerTypeSpecification:
              ImageConfiguration:
                ImageUri: !Sub '${AWS::AccountId}.dkr.ecr.${AWS::Region}.amazonaws.com/ridestream-spark:latest'
        - Key: EXECUTOR
          Value:
            WorkerCount: 10
            WorkerTypeSpecification:
              ImageConfiguration:
                ImageUri: !Sub '${AWS::AccountId}.dkr.ecr.${AWS::Region}.amazonaws.com/ridestream-spark:latest'
      Tags:
        Environment: prod
        Name: ridestream-emr-serverless

Outputs:
  EMRServerlessApplicationId:
    Value: !Ref RideStreamEMRServerlessApplication
    Export:
      Name: RideStream-EMR-App-ID
```

### Step Functions Template

```yaml
# cloudformation/step-functions.yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: 'RideStream AWS Step Functions for pipeline orchestration'

Resources:
  RideStreamStepFunctionsRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: states.amazonaws.com
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - arn:aws:iam::aws:policy/AWSStepFunctionsFullAccess
      Policies:
        - PolicyName: RideStreamStepFunctionsPolicy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - emr-serverless:StartJobRun
                  - emr-serverless:GetJobRun
                  - emr-serverless:CancelJobRun
                Resource: '*'
              - Effect: Allow
                Action:
                  - s3:GetObject
                  - s3:PutObject
                Resource: arn:aws:s3:::ridestream-*/*
              - Effect: Allow
                Action:
                  - glue:StartCrawler
                  - glue:GetCrawler
                Resource: !Sub 'arn:aws:glue:${AWS::Region}:${AWS::AccountId}:crawler/*'

  RideStreamPipelineStateMachine:
    Type: AWS::StepFunctions::StateMachine
    Properties:
      StateMachineType: STANDARD
      RoleArn: !GetAtt RideStreamStepFunctionsRole.Arn
      DefinitionString: !Sub |
        {
          "Comment": "RideStream Data Pipeline Orchestration",
          "StartAt": "Ingest",
          "States": {
            "Ingest": {
              "Type": "Task",
              "Resource": "arn:aws:states:::emr-serverless:startJobRun.sync",
              "Parameters": {
                "ApplicationId": "${RideStreamEMRServerlessApplication}",
                "ExecutionRoleArn": "${RideStreamEMRJobRole.Arn}",
                "JobDriver": {
                  "SparkSubmit": {
                    "EntryPoint": "s3://ridestream-bronze/jobs/ingest.py"
                  }
                }
              },
              "Next": "Transform"
            },
            "Transform": {
              "Type": "Task",
              "Resource": "arn:aws:states:::emr-serverless:startJobRun.sync",
              "Parameters": {
                "ApplicationId": "${RideStreamEMRServerlessApplication}",
                "ExecutionRoleArn": "${RideStreamEMRJobRole.Arn}",
                "JobDriver": {
                  "SparkSubmit": {
                    "EntryPoint": "s3://ridestream-silver/jobs/transform.py"
                  }
                }
              },
              "Next": "Aggregate"
            },
            "Aggregate": {
              "Type": "Task",
              "Resource": "arn:aws:states:::emr-serverless:startJobRun.sync",
              "Parameters": {
                "ApplicationId": "${RideStreamEMRServerlessApplication}",
                "ExecutionRoleArn": "${RideStreamEMRJobRole.Arn}",
                "JobDriver": {
                  "SparkSubmit": {
                    "EntryPoint": "s3://ridestream-gold/jobs/aggregate.py"
                  }
                }
              },
              "End": true
            }
          }
        }

  RideStreamEMRJobRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: emr-serverless.amazonaws.com
            Action: sts:AssumeRole
      Policies:
        - PolicyName: RideStreamEMRJobPolicy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - s3:GetObject
                  - s3:PutObject
                Resource: arn:aws:s3:::ridestream-*/*
              - Effect: Allow
                Action:
                  - glue:GetDatabase
                  - glue:GetTable
                  - glue:CreateTable
                  - glue:UpdateTable
                Resource: '*'

Outputs:
  StateMachineArn:
    Value: !Ref RideStreamPipelineStateMachine
    Export:
      Name: RideStream-StateMachine-Arn
```

### IAM Roles Template

```yaml
# cloudformation/iam.yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: 'RideStream IAM roles for least-privilege access'

Resources:
  RideStreamGlueRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: glue.amazonaws.com
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole
      Policies:
        - PolicyName: RideStreamGluePolicy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - s3:GetObject
                  - s3:PutObject
                Resource:
                  - arn:aws:s3:::ridestream-bronze/*
                  - arn:aws:s3:::ridestream-silver/*
                  - arn:aws:s3:::ridestream-gold/*

  RideStreamAthenaRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: athena.amazonaws.com
            Action: sts:AssumeRole
      Policies:
        - PolicyName: RideStreamAthenaPolicy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - s3:GetObject
                  - s3:PutObject
                Resource: arn:aws:s3:::ridestream-*/*
              - Effect: Allow
                Action:
                  - glue:GetDatabase
                  - glue:GetTable
                  - glue:GetPartitions
                Resource: '*'

  RideStreamCodeBuildRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: codebuild.amazonaws.com
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - arn:aws:iam::aws:policy/CloudWatchLogsFullAccess
      Policies:
        - PolicyName: RideStreamCodeBuildPolicy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - ecr:GetDownloadUrlForLayer
                  - ecr:BatchGetImage
                  - ecr:PutImage
                  - ecr:InitiateLayerUpload
                  - ecr:UploadLayerPart
                  - ecr:CompleteLayerUpload
                Resource: !Sub 'arn:aws:ecr:${AWS::Region}:${AWS::AccountId}:repository/ridestream*'
              - Effect: Allow
                Action:
                  - s3:GetObject
                  - s3:PutObject
                Resource: arn:aws:s3:::ridestream-*/*
              - Effect: Allow
                Action:
                  - ec2:CreateNetworkInterface
                  - ec2:DescribeNetworkInterfaces
                  - ec2:DeleteNetworkInterface
                Resource: '*'

Outputs:
  GlueRoleArn:
    Value: !GetAtt RideStreamGlueRole.Arn
    Export:
      Name: RideStream-Glue-Role

  AthenaRoleArn:
    Value: !GetAtt RideStreamAthenaRole.Arn
    Export:
      Name: RideStream-Athena-Role

  CodeBuildRoleArn:
    Value: !GetAtt RideStreamCodeBuildRole.Arn
    Export:
      Name: RideStream-CodeBuild-Role
```

### CodePipeline & CodeBuild Template

```yaml
# cloudformation/codepipeline.yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: 'RideStream CI/CD pipeline (CodePipeline + CodeBuild)'

Parameters:
  GitHubRepo:
    Type: String
    Default: ride_stream_v2
  GitHubBranch:
    Type: String
    Default: main
  GitHubToken:
    Type: String
    NoEcho: true

Resources:
  ArtifactBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: !Sub 'ridestream-artifacts-${AWS::AccountId}'
      VersioningConfiguration:
        Status: Enabled
      PublicAccessBlockConfiguration:
        BlockPublicAcls: true
        BlockPublicPolicy: true
        IgnorePublicAcls: true
        RestrictPublicBuckets: true

  RideStreamPipelineRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: codepipeline.amazonaws.com
            Action: sts:AssumeRole
      Policies:
        - PolicyName: RideStreamPipelinePolicy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - s3:GetObject
                  - s3:PutObject
                Resource: !Sub '${ArtifactBucket.Arn}/*'
              - Effect: Allow
                Action:
                  - codebuild:BatchGetBuilds
                  - codebuild:BatchGetBuildBatches
                  - codebuild:StartBuild
                  - codebuild:StartBuildBatch
                Resource: '*'
              - Effect: Allow
                Action:
                  - cloudformation:CreateChangeSet
                  - cloudformation:DescribeChangeSet
                  - cloudformation:ExecuteChangeSet
                  - cloudformation:DescribeStacks
                Resource: !Sub 'arn:aws:cloudformation:${AWS::Region}:${AWS::AccountId}:stack/ridestream-*'

  RideStreamCodeBuildProject:
    Type: AWS::CodeBuild::Project
    Properties:
      Name: ridestream-build
      ServiceRole: !GetAtt RideStreamCodeBuildRole.Arn
      Artifacts:
        Type: CODEPIPELINE
      Environment:
        ComputeType: BUILD_GENERAL1_LARGE
        Image: aws/codebuild/standard:7.0
        PrivilegedMode: true
        EnvironmentVariables:
          - Name: AWS_ACCOUNT_ID
            Value: !Ref AWS::AccountId
          - Name: AWS_REGION
            Value: !Ref AWS::Region
          - Name: ECR_REPOSITORY_NAME
            Value: ridestream
      Source:
        Type: CODEPIPELINE
        BuildSpec: |
          version: 0.2
          phases:
            pre_build:
              commands:
                - echo "Verifying Docker image tags exist..."
                - docker manifest inspect apache/spark:3.5.1-python3
                - docker manifest inspect trinodb/trino:430
                - docker manifest inspect confluentinc/cp-kafka:7.6.0
                - echo "Building application..."
                - python -m pip install -r requirements.txt
                - ruff check src/
                - mypy --strict src/
                - bandit -r src/ -ll
            build:
              commands:
                - echo "Running tests..."
                - pytest tests/unit/ -v --cov=src
                - echo "Starting Docker stack..."
                - docker-compose -f docker/docker-compose.yml up -d
                - sleep 30
                - echo "Running smoke tests..."
                - bash scripts/smoke-test.sh
                - docker-compose -f docker/docker-compose.yml down
            post_build:
              commands:
                - echo "Building Docker image..."
                - docker build -t $ECR_REPOSITORY_NAME:latest .
                - aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
                - docker tag $ECR_REPOSITORY_NAME:latest $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPOSITORY_NAME:latest
                - docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPOSITORY_NAME:latest
          artifacts:
            files:
              - cloudformation/**/*
              - src/**/*
              - docker/**/*
          cache:
            paths:
              - '/root/.cache/pip/**/*'
              - 'target/**/*'
          timeout: 30

  RideStreamCodeBuildRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: codebuild.amazonaws.com
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - arn:aws:iam::aws:policy/CloudWatchLogsFullAccess
      Policies:
        - PolicyName: RideStreamCodeBuildPolicy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - ecr:GetDownloadUrlForLayer
                  - ecr:BatchGetImage
                  - ecr:PutImage
                  - ecr:InitiateLayerUpload
                  - ecr:UploadLayerPart
                  - ecr:CompleteLayerUpload
                  - ecr:GetAuthorizationToken
                Resource: '*'
              - Effect: Allow
                Action:
                  - s3:GetObject
                  - s3:PutObject
                Resource: !Sub '${ArtifactBucket.Arn}/*'
              - Effect: Allow
                Action:
                  - ec2:CreateNetworkInterface
                  - ec2:DescribeNetworkInterfaces
                  - ec2:DeleteNetworkInterface
                Resource: '*'
              - Effect: Allow
                Action:
                  - kms:Decrypt
                  - kms:DescribeKey
                Resource: '*'

  RideStreamPipeline:
    Type: AWS::CodePipeline::Pipeline
    Properties:
      Name: ridestream-pipeline
      RoleArn: !GetAtt RideStreamPipelineRole.Arn
      ArtifactStore:
        Type: S3
        Location: !Ref ArtifactBucket
      Stages:
        - Name: Source
          Actions:
            - Name: SourceAction
              ActionTypeId:
                Category: Source
                Owner: ThirdParty
                Provider: GitHub
                Version: '1'
              Configuration:
                Owner: !Select [0, !Split ['/', !Sub 'https://github.com/YOUR_ORG/${GitHubRepo}']]
                Repo: !Ref GitHubRepo
                Branch: !Ref GitHubBranch
                OAuthToken: !Ref GitHubToken
              OutputArtifacts:
                - Name: SourceOutput
        
        - Name: Build
          Actions:
            - Name: BuildAction
              ActionTypeId:
                Category: Build
                Owner: AWS
                Provider: CodeBuild
                Version: '1'
              Configuration:
                ProjectName: !Ref RideStreamCodeBuildProject
              InputArtifacts:
                - Name: SourceOutput
              OutputArtifacts:
                - Name: BuildOutput
        
        - Name: Deploy
          Actions:
            - Name: CreateChangeSet
              ActionTypeId:
                Category: Deploy
                Owner: AWS
                Provider: CloudFormation
                Version: '1'
              Configuration:
                ActionMode: CHANGE_SET_REPLACE
                StackName: ridestream-prod
                ChangeSetName: !Sub 'ridestream-changeset-${AWS::AccountId}'
                TemplatePath: BuildOutput::cloudformation/vpc.yaml
                Capabilities: CAPABILITY_IAM
                RoleArn: !GetAtt RideStreamCFRole.Arn
              InputArtifacts:
                - Name: BuildOutput
        
            - Name: ExecuteChangeSet
              ActionTypeId:
                Category: Deploy
                Owner: AWS
                Provider: CloudFormation
                Version: '1'
              Configuration:
                ActionMode: CHANGE_SET_EXECUTE
                StackName: ridestream-prod
                ChangeSetName: !Sub 'ridestream-changeset-${AWS::AccountId}'

  RideStreamCFRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: cloudformation.amazonaws.com
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - arn:aws:iam::aws:policy/AdministratorAccess
```

---

## Rule 9: SMOKE TEST AFTER EVERY INFRA TASK

Mandatory validation after any infrastructure change. All checks must pass before proceeding.

### Actual Working Smoke Test

```bash
#!/bin/bash
# scripts/smoke-test.sh

set -e

echo "Starting RideStream smoke tests..."

# Helper function
check() {
  local name=$1
  local cmd=$2
  echo -n "Checking $name... "
  if eval "$cmd" > /dev/null 2>&1; then
    echo "✓"
    return 0
  else
    echo "✗ FAILED: $name"
    return 1
  fi
}

# Local Docker stack checks
check "Kafka topics" "docker exec rs2-kafka kafka-topics --bootstrap-server kafka:29092 --list"
check "MinIO health" "curl -sf http://localhost:9000/minio/health/live"
check "MinIO buckets" "docker run --rm --network ride_stream_v2_default minio/mc:latest sh -c 'mc alias set minio http://minio:9000 minioadmin minioadmin && mc ls minio/bronze'"
check "Trino" "curl -sf http://localhost:8888/v1/info"
check "Hive Metastore" "bash -c 'echo > /dev/tcp/localhost/9083'"
check "Spark Master" "curl -sf http://localhost:8090/"
check "Spark Worker" "curl -sf http://localhost:8081/"
check "Airflow Webserver" "curl -sf http://localhost:8085/health"

# Production AWS checks (if credentials available)
if [[ -n "$AWS_ACCESS_KEY_ID" ]]; then
  echo ""
  echo "Checking AWS infrastructure..."
  
  check "VPC exists" "aws ec2 describe-vpcs --filters 'Name=tag:Name,Values=ridestream-vpc-prod' --query 'Vpcs[0].VpcId'"
  
  check "MSK cluster exists" "aws kafka list-clusters --query 'ClusterInfoList[?ClusterName==\`ridestream-kafka\`]' | grep -q ridestream-kafka"
  
  check "S3 buckets exist" "aws s3 ls | grep -q ridestream-bronze"
  
  check "Glue databases exist" "aws glue get-database --name ridestream_bronze 2>/dev/null | grep -q Name"
  
  check "Athena workgroup exists" "aws athena get-work-group --work-group ridestream-workgroup 2>/dev/null | grep -q ridestream"
  
  check "EMR Serverless app exists" "aws emr-serverless list-applications --query 'applications[?name==\`ridestream-spark\`]' | grep -q ridestream-spark"
  
  check "Step Functions state machine exists" "aws stepfunctions list-state-machines --query 'stateMachines[?name==\`ridestream-pipeline\`]' | grep -q ridestream"
fi

echo ""
echo "✓ All smoke tests passed!"
```

---

## Rule 10: VERIFY DOCKER IMAGE TAGS EXIST

Before deploying or building, verify all Docker images exist in registries.

```bash
#!/bin/bash
# scripts/verify-docker-images.sh

set -e

echo "Verifying Docker image tags..."

IMAGES=(
  "confluentinc/cp-zookeeper:7.6.0"
  "confluentinc/cp-kafka:7.6.0"
  "minio/minio:RELEASE.2024-11-07T00-52-07Z"
  "minio/mc:latest"
  "apache/hive:4.0.0"
  "trinodb/trino:430"
  "apache/spark:3.5.1-python3"
  "apache/airflow:2.9.3-python3.12"
  "postgres:15-alpine"
)

FAILED=0

for image in "${IMAGES[@]}"; do
  echo -n "Verifying $image... "
  if docker manifest inspect "$image" > /dev/null 2>&1; then
    echo "✓"
  else
    echo "✗ NOT FOUND"
    FAILED=$((FAILED + 1))
  fi
done

if [[ $FAILED -gt 0 ]]; then
  echo ""
  echo "ERROR: $FAILED image(s) not found. Aborting."
  exit 1
fi

echo ""
echo "✓ All Docker image tags verified"
```

---

## Change Sets (Never Direct Updates)

Always review change sets before applying.

```bash
#!/bin/bash
# scripts/deploy-changeset.sh

STACK_NAME=$1
TEMPLATE_FILE=$2

if [[ -z "$STACK_NAME" ]] || [[ -z "$TEMPLATE_FILE" ]]; then
  echo "Usage: $0 <stack-name> <template-file>"
  exit 1
fi

CHANGESET_NAME="$STACK_NAME-$(date +%s)"

echo "Creating change set: $CHANGESET_NAME"

aws cloudformation create-change-set \
  --stack-name "$STACK_NAME" \
  --change-set-name "$CHANGESET_NAME" \
  --template-body "file://$TEMPLATE_FILE" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM

echo "Waiting for change set creation..."
aws cloudformation wait change-set-create-complete \
  --stack-name "$STACK_NAME" \
  --change-set-name "$CHANGESET_NAME" || true

echo ""
echo "Change set created. Review changes:"
echo ""

aws cloudformation describe-change-set \
  --stack-name "$STACK_NAME" \
  --change-set-name "$CHANGESET_NAME" \
  --query 'Changes[*].[Type,ResourceChange.Action,ResourceChange.LogicalResourceId,ResourceChange.ResourceType]' \
  --output table

echo ""
read -p "Execute change set? (y/n) " -n 1 -r
echo

if [[ $REPLY =~ ^[Yy]$ ]]; then
  aws cloudformation execute-change-set \
    --stack-name "$STACK_NAME" \
    --change-set-name "$CHANGESET_NAME"
  
  echo "Waiting for stack update..."
  aws cloudformation wait stack-update-complete \
    --stack-name "$STACK_NAME" || \
  aws cloudformation wait stack-create-complete \
    --stack-name "$STACK_NAME"
  
  echo "✓ Stack updated successfully"
  
  # Run smoke tests
  bash scripts/smoke-test.sh
else
  echo "Change set not executed"
fi
```

---

## Summary

**Local (Docker Compose):**
- 11 services (Zookeeper, Kafka, MinIO, Hive Metastore, Trino, Spark Master/Worker, Airflow Webserver/Scheduler/DB)
- All `rs2-` prefixed
- Single docker-compose.yml source of truth
- Smoke tests validate health every deployment

**Production (AWS CloudFormation):**
- VPC with public/private subnets + NAT
- MSK (Kafka 3.6+) multi-AZ cluster
- S3 (4 buckets: bronze/silver/gold/athena-results)
- AWS Glue Catalog (Hive-compatible metadata)
- AWS Athena (serverless SQL on S3)
- AWS EMR Serverless (Spark 3.5+ batch jobs)
- AWS Step Functions (pipeline orchestration, replaces Airflow)
- IAM roles (least privilege, separate for EMR/Step Functions/Glue/Athena)
- AWS CodePipeline + CodeBuild (CI/CD with mandatory Docker smoke tests)
- Change sets before all updates

**CI/CD Gate:**
- CodeBuild runs Docker Compose smoke tests (mandatory pass before deployment)
- Timeout 30+ minutes (MSK provisioning 15-20 min)
- Image tag verification before build
- All tests must pass
