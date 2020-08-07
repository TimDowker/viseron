import logging
import signal
from queue import Queue
from threading import Thread

from lib.cleanup import Cleanup
from lib.config import ViseronConfig
from lib.detector import Detector
from lib.mqtt import MQTT
from lib.nvr import FFMPEGNVR

LOGGER = logging.getLogger()

LEVELS = {
    "CRITICAL": 50,
    "ERROR": 40,
    "WARNING": 30,
    "INFO": 20,
    "DEBUG": 10,
    "NOTSET": 0,
}


def main():
    # Initialize logging
    config = ViseronConfig.config

    log_settings(config)
    LOGGER.info("-------------------------------------------")
    LOGGER.info("Initializing...")

    schedule_cleanup(config)

    mqtt_queue = Queue(maxsize=50)
    mqtt = MQTT(config)
    mqtt_publisher = Thread(target=mqtt.publisher, args=(mqtt_queue,))
    mqtt_publisher.daemon = True
    mqtt_publisher.start()

    detector_queue = Queue(maxsize=2)
    detector = Detector(config)
    detector_thread = Thread(target=detector.object_detection, args=(detector_queue,))
    detector_thread.daemon = True
    detector_thread.start()

    threads = []
    for camera in config.cameras:
        threads.append(
            FFMPEGNVR(mqtt, mqtt_queue, ViseronConfig(camera), detector_queue)
        )

    mqtt.connect()

    for thread in threads:
        thread.start()

    LOGGER.info("Initialization complete")

    def signal_term(*_):
        LOGGER.info("Kill received! Sending kill to threads..")
        for thread in threads:
            thread.stop()
            thread.join()

    # Listen to sigterm
    signal.signal(signal.SIGTERM, signal_term)

    try:
        threads[0].join()
    except KeyboardInterrupt:
        LOGGER.info("Ctrl-C received! Sending kill to threads..")
        for thread in threads:
            thread.stop()
            thread.join()

    LOGGER.info("Exiting")


def schedule_cleanup(config):
    LOGGER.info("Starting cleanup scheduler")
    cleanup = Cleanup(config)
    cleanup.scheduler.start()
    LOGGER.info("Running initial cleanup")
    cleanup.cleanup()


def log_settings(config):
    LOGGER.setLevel(LEVELS[config.logging.level])
    formatter = logging.Formatter(
        "[%(asctime)s] [%(name)-12s] [%(levelname)-8s] - %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    formatter = MyFormatter()
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(DuplicateFilter())
    LOGGER.addHandler(handler)


class MyFormatter(logging.Formatter):
    # pylint: disable=protected-access
    overwrite_fmt = (
        "\x1b[80D\x1b[1A\x1b[K[%(asctime)s] "
        "[%(name)-12s] [%(levelname)-8s] - %(message)s"
    )

    def __init__(self):
        super().__init__(
            fmt="[%(asctime)s] [%(name)-12s] [%(levelname)-8s] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            style="%",
        )
        self.current_count = 0

    def format(self, record):
        # Save the original format configured by the user
        # when the logger formatter was instantiated
        format_orig = self._style._fmt

        # Replace the original format with one customized by logging level
        if "message repeated" in str(record.msg):
            self._style._fmt = MyFormatter.overwrite_fmt

        # Call the original formatter class to do the grunt work
        result = logging.Formatter.format(self, record)

        # Restore the original format configured by the user
        self._style._fmt = format_orig

        return result


class DuplicateFilter(logging.Filter):
    # pylint: disable=attribute-defined-outside-init
    def filter(self, record):
        current_log = (record.module, record.levelno, record.msg)
        if current_log != getattr(self, "last_log", None):
            self.last_log = current_log
            self.current_count = 0
        else:
            self.current_count += 1
            if self.current_count > 0:
                record.msg = "{}, message repeated {} times".format(
                    record.msg, self.current_count + 1
                )
        return True


if __name__ == "__main__":
    main()