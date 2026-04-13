# Custom Hive Metastore with hadoop-aws JARs for S3A compatibility
# ADR-012: apache/hive:4.0.0 doesn't include hadoop-aws by default.
# Without these JARs, S3A operations fail:
#   java.io.IOException: No FileSystem for scheme s3a
#
# See: TECHNICAL_PLAN.md §10.3

FROM apache/hive:4.0.0

USER root

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    curl -fSL https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.3.6/hadoop-aws-3.3.6.jar \
      -o $HADOOP_HOME/share/hadoop/common/hadoop-aws-3.3.6.jar && \
    curl -fSL https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.12.565/aws-java-sdk-bundle-1.12.565.jar \
      -o $HADOOP_HOME/share/hadoop/common/aws-java-sdk-bundle-1.12.565.jar && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

ENV HADOOP_CLASSPATH=$HADOOP_HOME/share/hadoop/common/*:$HADOOP_CLASSPATH

COPY hive-site.xml /opt/hive/conf/hive-site.xml

USER hive

EXPOSE 9083

CMD ["hive", "--service", "metastore"]
