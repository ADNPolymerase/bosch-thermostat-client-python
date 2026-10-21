"""The gas usage recording must survive the day a page rolls over."""

import json
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bosch_thermostat_client.const.nefit import NEFIT
from bosch_thermostat_client.sensors import Sensors

DB_DIR = Path(__file__).resolve().parents[1] / "bosch_thermostat_client" / "db"

POINTER_URI = "/ecus/rrc/recordings/gasusagePointer"
PAGE_SIZE = 32
EMPTY_ROW = {"d": "255-256-65535", "hw": 6553.5, "ch": 6553.5, "T": -1}


def load_json(path):
    return json.loads(Path(path).read_text())


def nefit_sensors(responses, db_file="022200.json"):
    """Build the NEFIT sensors from the shipped database with a fake connector."""

    async def get(path):
        return responses[path]

    connector = SimpleNamespace(device_type=NEFIT, get=AsyncMock(side_effect=get))
    sensors = Sensors(
        connector=connector,
        sensors_db=load_json(DB_DIR / "nefit" / db_file)["sensors"],
        errors=load_json(DB_DIR / "errorcodes_nefit.json"),
    )
    return connector, {sensor.attr_id: sensor for sensor in sensors}


def pointer(value):
    return {
        "id": POINTER_URI,
        "type": "floatValue",
        "recordable": 0,
        "writeable": 0,
        "value": value,
        "unitOfMeasure": "",
        "minValue": 1,
        "maxValue": 6400,
    }


def page(rows):
    return {
        "id": "/ecus/rrc/recordings/gasusage",
        "type": "recordings",
        "recordable": 0,
        "writeable": 0,
        "value": rows + [EMPTY_ROW] * (PAGE_SIZE - len(rows)),
    }


def day(date, hw, ch):
    return {"d": date, "hw": hw, "ch": ch, "T": 184}


class EcusRecordingPageTests(unittest.IsolatedAsyncioTestCase):
    now = datetime(2026, 9, 24, 13, 0)
    yesterday = day("23-09-2026", hw=7.2, ch=0)

    async def test_reads_the_last_day_of_the_current_page(self):
        connector, sensors = nefit_sensors(
            {
                POINTER_URI: pointer(1696),
                "/ecus/rrc/recordings/gasusage?page=53": page(
                    [day("22-09-2026", hw=5.5, ch=0), self.yesterday]
                ),
            }
        )
        sensor = sensors["ecus_recording"]

        await sensor.update(self.now)

        self.assertEqual(sensor.state, self.yesterday)
        self.assertEqual(connector.get.await_count, 2)

    async def test_falls_back_to_the_previous_page_when_a_page_rolls_over(self):
        # 1697 is the first slot of page 54, so that page holds no day yet and
        # the last recorded day is the last row of page 53.
        connector, sensors = nefit_sensors(
            {
                POINTER_URI: pointer(1697),
                "/ecus/rrc/recordings/gasusage?page=54": page([]),
                "/ecus/rrc/recordings/gasusage?page=53": page(
                    [day("22-09-2026", hw=5.5, ch=0), self.yesterday]
                ),
            }
        )
        sensor = sensors["ecus_recording"]

        await sensor.update(self.now)

        self.assertEqual(sensor.state, self.yesterday)
        self.assertEqual(sensor.last_entry["23-09-2026"], self.yesterday)

    async def test_keeps_no_value_when_neither_page_holds_the_day(self):
        connector, sensors = nefit_sensors(
            {
                POINTER_URI: pointer(1697),
                "/ecus/rrc/recordings/gasusage?page=54": page([]),
                "/ecus/rrc/recordings/gasusage?page=53": page([]),
            }
        )
        sensor = sensors["ecus_recording"]

        await sensor.update(self.now)

        self.assertIsNone(sensor.state)

    async def test_does_not_read_page_zero(self):
        connector, sensors = nefit_sensors(
            {
                POINTER_URI: pointer(1),
                "/ecus/rrc/recordings/gasusage?page=1": page([]),
            }
        )
        sensor = sensors["ecus_recording"]

        await sensor.update(self.now)

        self.assertNotIn(
            "/ecus/rrc/recordings/gasusage?page=0",
            [call.args[0] for call in connector.get.await_args_list],
        )


if __name__ == "__main__":
    unittest.main()
