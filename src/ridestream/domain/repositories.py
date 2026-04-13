"""Abstract port interfaces for domain repositories.

Defines contracts that adapters must implement.
Both local (mock/file/Docker) and AWS adapters implement these.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ridestream.domain.entities import Ride, RideEvent
from ridestream.domain.value_objects import RideID, RideStatus


class RideRepository(ABC):
    """Port for ride persistence.

    Implementations: S3RideRepository (AWS), MinIORideRepository (local).
    """

    @abstractmethod
    def save(self, ride: Ride) -> None:
        """Persist a ride.

        Args:
            ride: The ride to save.
        """

    @abstractmethod
    def get_by_id(self, ride_id: RideID) -> Ride | None:
        """Fetch a ride by ID.

        Args:
            ride_id: The ride identifier.

        Returns:
            The ride, or None if not found.
        """

    @abstractmethod
    def list_by_status(self, status: RideStatus) -> list[Ride]:
        """List rides with a given status.

        Args:
            status: The status to filter by.

        Returns:
            List of matching rides.
        """


class EventPublisher(ABC):
    """Port for publishing domain events to external systems.

    Implementations: KafkaEventPublisher (local), MSKEventPublisher (AWS).
    """

    @abstractmethod
    def publish(self, event: RideEvent) -> None:
        """Publish a domain event.

        Args:
            event: The event to publish.
        """


class EventStore(ABC):
    """Port for event sourcing storage.

    Implementations: KafkaEventStore (local), MSKEventStore (AWS).
    """

    @abstractmethod
    def append(self, event: RideEvent) -> None:
        """Append an event to the store.

        Args:
            event: The event to append.
        """

    @abstractmethod
    def get_events_for_ride(self, ride_id: RideID) -> list[RideEvent]:
        """Get all events for a ride, in chronological order.

        Args:
            ride_id: The ride identifier.

        Returns:
            Ordered list of events for this ride.
        """


class CatalogAdapter(ABC):
    """Port for data catalog operations (table registration, schema queries).

    Implementations: GlueCatalogAdapter (AWS), HiveCatalogAdapter (local).
    """

    @abstractmethod
    def create_table(
        self,
        database: str,
        table: str,
        columns: list[dict[str, str]],
        location: str,
    ) -> None:
        """Register a table in the catalog.

        Args:
            database: Database/schema name.
            table: Table name.
            columns: Column definitions [{"name": ..., "type": ...}].
            location: Data location (S3 path or local path).
        """

    @abstractmethod
    def get_table_schema(
        self,
        database: str,
        table: str,
    ) -> list[dict[str, str]]:
        """Get schema for a registered table.

        Args:
            database: Database/schema name.
            table: Table name.

        Returns:
            Column definitions [{"name": ..., "type": ...}].
        """


class QueryEngine(ABC):
    """Port for SQL query execution.

    Implementations: AthenaQueryEngine (AWS), TrinoQueryEngine (local).
    """

    @abstractmethod
    def execute(self, sql: str) -> list[dict[str, object]]:
        """Execute a SQL query and return results.

        Args:
            sql: The SQL query string.

        Returns:
            List of row dictionaries.
        """
