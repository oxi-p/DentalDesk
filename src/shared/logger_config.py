import logging
import sys
import os

def setup_logging(file_level=logging.DEBUG, console_level=logging.INFO):
    """
    Configures the root logger for the application.

    This function sets up two handlers: one for writing to a file and one for
    writing to stdout. Each can have a different logging level.

    Args:
        file_level: The logging level for the file handler.
        console_level: The logging level for the console (stdout) handler.
    """
    # Define the logs directory path relative to the project root (src/..)
    log_dir = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file_path = os.path.join(log_dir, "app.log")

    # Get the root logger
    root_logger = logging.getLogger()
    # Set the root logger level to the lowest of the two handlers
    # to ensure it passes all messages to the handlers.
    root_logger.setLevel(min(file_level, console_level))

    # Clear existing handlers to prevent duplicate logs
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # Create a standard formatter
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Create and add the file handler with its own level
    file_handler = logging.FileHandler(log_file_path, mode='a')
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Create and add the stream handler (for stdout) with its own level
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(console_level)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    logging.info("Logging configured. File level: %s, Console level: %s",
                 logging.getLevelName(file_level), logging.getLevelName(console_level))
