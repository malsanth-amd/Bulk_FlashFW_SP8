"""Regression test for the real bug hit via the web app: ac_cycle()'s
manual-prompt fallback crashed with a raw, confusing EOFError when run as
a background subprocess with closed stdin (always true for a web- or
scheduler-triggered flash - JobManager deliberately uses stdin=DEVNULL).
It must now raise a clear, actionable RuntimeError instead."""

from argparse import Namespace
from unittest.mock import patch

import flash_system as m


def _args_with_no_automated_ac_cycle():
    return Namespace(
        ac_cycle_cmd=None,
        fvddr_script=None,
        no_fvddr_ac_cycle=True,  # force past the FVDDR branch regardless of whether ac_cycle.py exists on disk
        pdu_vendor=None,
        pdu_ip=None,
        system_name="test-system",
    )


def test_eof_on_manual_prompt_raises_clear_error_not_raw_eof():
    args = _args_with_no_automated_ac_cycle()
    with patch("builtins.input", side_effect=EOFError):
        try:
            m.ac_cycle(args, bmc_ip="10.0.0.1", bmc_user="root", bmc_pass="x")
            assert False, "should have raised"
        except RuntimeError as e:
            msg = str(e)
            assert "No automated AC-cycle method is configured" in msg, msg
            assert "web-triggered flash" in msg, msg
        except EOFError:
            assert False, "raw EOFError leaked through - the whole point of this fix is to replace it"
    print("PASS: closed stdin during the manual AC-cycle prompt raises a clear, actionable RuntimeError")


def test_real_user_present_still_works_normally():
    args = _args_with_no_automated_ac_cycle()
    with patch("builtins.input", return_value="") as mock_input:
        m.ac_cycle(args, bmc_ip="10.0.0.1", bmc_user="root", bmc_pass="x")
        mock_input.assert_called_once()
    print("PASS: a real interactive terminal (input() returns normally) is unaffected by this fix")


if __name__ == "__main__":
    test_eof_on_manual_prompt_raises_clear_error_not_raw_eof()
    test_real_user_present_still_works_normally()
    print("\nALL PASS")
