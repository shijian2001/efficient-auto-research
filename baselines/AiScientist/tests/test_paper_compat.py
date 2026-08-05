from __future__ import annotations

import asyncio

from aisci_domain_paper.paper_compat import LocalComputerInterface, send_shell_command_with_timeout


def test_local_computer_interface_shell_command() -> None:
    result = asyncio.run(LocalComputerInterface("/tmp").send_shell_command("printf 'hello'"))
    assert result.exit_code == 0
    assert result.unicode_output_best_effort == "hello"


def test_send_shell_command_with_timeout_kills_process() -> None:
    result = asyncio.run(
        send_shell_command_with_timeout(
            LocalComputerInterface("/tmp"),
            "sleep 2",
            timeout=1,
        )
    )
    assert result.exit_code == 137
