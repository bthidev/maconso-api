#!/usr/bin/env python3
"""
Simple Daily Energy Data Pipeline - Fetches yesterday's data and imports to InfluxDB
"""

import json
import logging
import os
import requests
from datetime import datetime, timedelta
from typing import Set, Tuple

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS


def get_env_var(var_name: str, default: str | None = None, required: bool = True) -> str:
    """Get environment variable with validation"""
    value = os.getenv(var_name, default)
    if required and not value:
        raise ValueError(f"Required environment variable {var_name} is not set")
    return value


def get_env_float(
    var_name: str, default: float | None = None, required: bool = True
) -> float:
    """Get float environment variable"""
    value = os.getenv(var_name)
    if not value:
        if required and default is None:
            raise ValueError(f"Required environment variable {var_name} is not set")
        return default
    try:
        return float(value)
    except ValueError:
        raise ValueError(
            f"Environment variable {var_name} must be a float, got: {value}"
        )


# Configuration from environment variables
API_URL = get_env_var("API_URL", "https://conso.boris.sh/api/:type", required=False)
USAGE_POINT_ID = get_env_var("USAGE_POINT_ID")
BEARER_TOKEN = get_env_var("BEARER_TOKEN")
RATE_LIMIT_DELAY = get_env_float("RATE_LIMIT_DELAY", 1.0, required=False)

INFLUXDB_URL = get_env_var("INFLUXDB_URL", "http://localhost:8086", required=False)
INFLUXDB_TOKEN = get_env_var("INFLUXDB_TOKEN")
INFLUXDB_ORG = get_env_var("INFLUXDB_ORG")
INFLUXDB_BUCKET = get_env_var("INFLUXDB_BUCKET", "energy_data", required=False)

LOG_LEVEL = get_env_var("LOG_LEVEL", "INFO", required=False).upper()

# Setup logging
numeric_level = getattr(logging, LOG_LEVEL, None)
if not isinstance(numeric_level, int):
    raise ValueError(f"Invalid log level: {LOG_LEVEL}")

logging.basicConfig(
    level=numeric_level, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class SimpleDailyPipeline:
    """Daily energy data pipeline for fetching and importing yesterday's data"""

    def __init__(self):
        self.client = None
        self.write_api = None
        self.query_api = None

    def connect_influxdb(self) -> bool:
        """Connect to InfluxDB"""
        try:
            self.client = InfluxDBClient(
                url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG
            )
            self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
            self.query_api = self.client.query_api()

            # Test connection
            health = self.client.health()
            if health.status == "pass":
                logger.info("Connected to InfluxDB successfully")
                return True
            else:
                logger.error(f"InfluxDB health check failed: {health.message}")
                return False

        except Exception as e:
            logger.error(f"Failed to connect to InfluxDB: {e}")
            return False

    def get_existing_timestamps_for_date(self, date: datetime) -> Set[str]:
        """Get all existing timestamps for the given date from InfluxDB"""
        try:
            start_time = date.strftime("%Y-%m-%dT00:00:00Z")
            end_time = (date + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")

            query = f'''
            from(bucket: "{INFLUXDB_BUCKET}")
              |> range(start: {start_time}, stop: {end_time})
              |> filter(fn: (r) => r["_measurement"] == "energy_consumption")
              |> filter(fn: (r) => r["_field"] == "power")
              |> filter(fn: (r) => r["usage_point_id"] == "{USAGE_POINT_ID}")
              |> keep(columns: ["_time"])
            '''

            tables = self.query_api.query(query, org=INFLUXDB_ORG)
            existing_timestamps = set()

            for table in tables:
                for record in table.records:
                    timestamp = record.get_time()
                    if timestamp:
                        # Convert to the same format used in API data
                        timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
                        existing_timestamps.add(timestamp_str)

            logger.info(f"Found {len(existing_timestamps)} existing records for {date.date()}")
            return existing_timestamps

        except Exception as e:
            logger.warning(f"Could not fetch existing data for {date.date()}: {e}")
            return set()

    def fetch_yesterday_data(self) -> tuple[dict | None, datetime | None]:
        """Fetch yesterday's data from API"""
        yesterday = datetime.now() - timedelta(days=1)
        start_date = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)

        params = {
            "prm": USAGE_POINT_ID,
            "start": start_date.strftime("%Y-%m-%d"),
            "end": end_date.strftime("%Y-%m-%d"),
        }

        headers = {
            "Authorization": f"Bearer {BEARER_TOKEN}",
            "Content-Type": "application/json",
        }

        try:
            logger.info(f"Fetching data for {start_date.date()}")
            response = requests.get(API_URL, params=params, headers=headers, timeout=30)
            response.raise_for_status()

            data = response.json()
            readings_count = len(data.get("interval_reading", []))
            logger.info(f"Retrieved {readings_count} readings from API")

            return data, start_date

        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            return None, None
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            return None, None

    def convert_to_influx_points(self, data: dict, existing_timestamps: Set[str]) -> Tuple[list[Point], int, int]:
        """
        Convert API data to InfluxDB points, filtering out existing timestamps
        Returns: (points_to_insert, total_api_records, skipped_existing_records)
        """
        if not data or "interval_reading" not in data:
            return [], 0, 0

        points = []
        total_records = 0
        skipped_records = 0

        for reading in data["interval_reading"]:
            total_records += 1
            try:
                # Parse timestamp
                timestamp_str = reading["date"]
                timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")

                # Check if this timestamp already exists
                if timestamp_str in existing_timestamps:
                    skipped_records += 1
                    logger.debug(f"Skipping existing record for {timestamp_str}")
                    continue

                # Parse value
                value = float(reading["value"])

                # Create InfluxDB point
                point = (
                    Point("energy_consumption")
                    .tag("usage_point_id", USAGE_POINT_ID)
                    .tag("measure_type", reading.get("measure_type", ""))
                    .tag("interval_length", reading.get("interval_length", ""))
                    .field("power", value)
                    .time(timestamp, WritePrecision.S)
                )

                points.append(point)

            except (ValueError, TypeError, KeyError) as e:
                logger.warning(f"Skipping invalid reading: {e}")
                continue

        return points, total_records, skipped_records

    def write_to_influxdb(self, points: list[Point]) -> bool:
        """Write points to InfluxDB"""
        if not points:
            logger.info("No new data points to write")
            return True

        try:
            self.write_api.write(
                bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=points
            )
            logger.info(f"Successfully wrote {len(points)} new points to InfluxDB")
            return True

        except Exception as e:
            logger.error(f"Failed to write to InfluxDB: {e}")
            return False

    def run(self) -> bool:
        """Main execution function"""
        logger.info("Starting daily energy data pipeline for yesterday")

        # Connect to InfluxDB
        if not self.connect_influxdb():
            return False

        try:
            # Get yesterday's date
            yesterday = datetime.now() - timedelta(days=1)
            yesterday = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)

            # Get existing timestamps from InfluxDB
            existing_timestamps = self.get_existing_timestamps_for_date(yesterday)

            # Fetch data from API
            data, date = self.fetch_yesterday_data()
            if not data:
                logger.error("Failed to fetch data from API")
                return False

            # Convert to InfluxDB points, filtering out existing data
            points, total_api_records, skipped_records = self.convert_to_influx_points(data, existing_timestamps)
            
            logger.info(f"API returned {total_api_records} records")
            logger.info(f"Skipped {skipped_records} existing records")
            logger.info(f"Found {len(points)} new records to import")

            if not points:
                logger.info("No new data to import - all records already exist in InfluxDB")
                return True

            # Write new points to InfluxDB
            success = self.write_to_influxdb(points)

            if success:
                logger.info(
                    f"Successfully imported {len(points)} new data points for {date.date()}"
                )

            return success

        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return False

        finally:
            if self.client:
                self.client.close()


def main() -> int:
    """Entry point"""
    try:
        # Load environment variables from .env file if available
        try:
            from dotenv import load_dotenv

            load_dotenv()
            logger.debug("Loaded environment variables from .env file")
        except ImportError:
            logger.debug(
                "python-dotenv not installed, using system environment variables"
            )

        pipeline = SimpleDailyPipeline()
        success = pipeline.run()

        if success:
            logger.info("Pipeline completed successfully")
            return 0
        else:
            logger.error("Pipeline failed")
            return 1

    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
        return 1
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())