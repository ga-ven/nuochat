import unittest
from unittest.mock import patch

from nuochat.power import ES_CONTINUOUS, ES_SYSTEM_REQUIRED, set_sleep_inhibition


class PowerTests(unittest.TestCase):
    def test_inhibit_sleep_without_requiring_display(self):
        with patch("nuochat.power._kernel32") as kernel32:
            kernel32.SetThreadExecutionState.return_value = 1
            self.assertTrue(set_sleep_inhibition(True))
            kernel32.SetThreadExecutionState.assert_called_once_with(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED,
            )

    def test_restore_power_policy(self):
        with patch("nuochat.power._kernel32") as kernel32:
            kernel32.SetThreadExecutionState.return_value = 1
            self.assertTrue(set_sleep_inhibition(False))
            kernel32.SetThreadExecutionState.assert_called_once_with(ES_CONTINUOUS)


if __name__ == "__main__":
    unittest.main()
