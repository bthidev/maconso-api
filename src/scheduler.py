#!/usr/bin/env python3
"""
Python-based scheduler for the energy pipeline
Alternative to cron that doesn't require root privileges
"""

import schedule
import time
import sys
import signal
import logging
import traceback
from datetime import datetime
import os
from src.pipeline import main

# Add the project root to Python path
sys.path.insert(0, '/app')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/maconso/scheduler.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

class PipelineScheduler:
    def __init__(self):
        self.running = True
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
        
    def signal_handler(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False
        
    def run_pipeline(self):
        """Execute the energy pipeline by importing and running pipeline.main directly"""
        try:
            logger.info("Starting energy pipeline execution...")
            start_time = datetime.now()
            
            # Import and run the pipeline.main function directly
            main()
            
            end_time = datetime.now()
            duration = end_time - start_time
            logger.info(f"Pipeline completed successfully in {duration.total_seconds():.2f} seconds")
                    
        except Exception as e:
            logger.error(f"Pipeline execution failed: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            # Don't re-raise the exception to keep the scheduler running
            
    def start(self, run_on_startup=False):
        """Start the scheduler"""
        logger.info("===== Maconso Energy Pipeline Scheduler =====")
        logger.info("Scheduled to run daily at 10:30 AM UTC")
        logger.info("Logs: /var/log/maconso/scheduler.log")
        logger.info("============================================")
        
        # Schedule the job for 2:00 AM daily
        schedule.every().day.at("10:30").do(self.run_pipeline)
        
        # Run once on startup if requested
        if run_on_startup:
            logger.info("Running pipeline on startup...")
            self.run_pipeline()
            
        # Main scheduler loop
        logger.info("Scheduler started. Press Ctrl+C to stop.")
        while self.running:
            try:
                schedule.run_pending()
                time.sleep(30)  # Check every 30 seconds
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                time.sleep(60)  # Wait a bit before retrying
                
        logger.info("Scheduler stopped.")

if __name__ == "__main__":
    import os
    run_on_startup = os.getenv("RUN_ON_STARTUP", "false").lower() == "true"
    
    scheduler = PipelineScheduler()
    scheduler.start(run_on_startup=run_on_startup)