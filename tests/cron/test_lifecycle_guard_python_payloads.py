"""Python ``-c`` sources and Python heredoc bodies are interpreter data, not shell source.

The executed referenced-script walk must not shell-tokenize them (a ``sys.path.insert(0, '<dir>')``
or ``open('<data file>')`` line read as a referenced script), while every real shell lifecycle
command around them stays blocked. The direct lifecycle scan still reads them unmasked: a Python
payload reaches a shell at runtime (``os.system``), so an inline lifecycle command in it is blocked
(#127698).
"""

import pytest


class TestLifecycleGuardPythonPayloads:
    def test_multiline_python_c_payload_with_directory_not_blocked(self, tmp_path):
        """Python ``-c`` source is interpreter data, not shell source."""
        from cron.lifecycle_guard import (
            contains_gateway_lifecycle_command_or_referenced_script,
        )
        real_directory = tmp_path / "real-directory"
        real_directory.mkdir()
        command = f'''python3 -c "
import os, sys
sys.path.insert(0, os.path.expanduser('{real_directory}'))
"'''

        assert contains_gateway_lifecycle_command_or_referenced_script(
            command, cwd=str(tmp_path)
        ) is False

    def test_multiline_single_quoted_python_c_payload_not_blocked(self, tmp_path):
        from cron.lifecycle_guard import (
            contains_gateway_lifecycle_command_or_referenced_script,
        )
        real_directory = tmp_path / "real-directory"
        real_directory.mkdir()
        command = f"""python -c '
import os, sys
sys.path.insert(0, os.path.expanduser("{real_directory}"))
'
"""

        assert contains_gateway_lifecycle_command_or_referenced_script(
            command, cwd=str(tmp_path)
        ) is False

    @pytest.mark.parametrize("operator", ["- <<'PY'", "<<PY"])
    def test_python_heredoc_payload_with_directory_not_blocked(
        self, tmp_path, operator
    ):
        """Quoted and bare Python heredocs stay out of the shell walk."""
        from cron.lifecycle_guard import (
            contains_gateway_lifecycle_command_or_referenced_script,
        )
        real_directory = tmp_path / "real-directory"
        real_directory.mkdir()
        command = f"""python3 {operator}
import os, sys
sys.path.insert(0, os.path.expanduser('{real_directory}'))
PY
"""

        assert contains_gateway_lifecycle_command_or_referenced_script(
            command, cwd=str(tmp_path)
        ) is False

    def test_shell_prefix_before_python_heredoc_is_scanned(self, tmp_path):
        """Only the Python heredoc body is excluded from shell scanning."""
        from cron.lifecycle_guard import (
            contains_gateway_lifecycle_command_or_referenced_script,
        )
        python_command = """cd /tmp && python3 - <<-'PY'
\tprint('ok')
PY
"""

        assert contains_gateway_lifecycle_command_or_referenced_script(
            python_command, cwd=str(tmp_path)
        ) is False
        assert contains_gateway_lifecycle_command_or_referenced_script(
            "cd /tmp && hermes gateway restart", cwd=str(tmp_path)
        ) is True

    @pytest.mark.parametrize("command", [
        "hermes gateway restart",
        "hermes gateway stop",
        "systemctl --user restart hermes-gateway.service",
        "systemctl --user stop hermes-gateway-worker.service",
        "launchctl submit -l com.example.worker -- /bin/true",
        "launchctl bootstrap gui/501 /tmp/com.example.worker.plist",
        'bash -c "hermes gateway restart"',
        "printf 'before\\n'\nhermes gateway stop",
    ])
    def test_terminal_lifecycle_detections_remain_blocked(self, tmp_path, command):
        from cron.lifecycle_guard import (
            contains_gateway_lifecycle_command_or_referenced_script,
        )

        assert contains_gateway_lifecycle_command_or_referenced_script(
            command, cwd=str(tmp_path)
        ) is True

    def test_nested_referenced_shell_lifecycle_remains_blocked(self, tmp_path):
        from cron.lifecycle_guard import (
            contains_gateway_lifecycle_command_or_referenced_script,
        )
        wrapper = tmp_path / "wrapper.sh"
        deploy = tmp_path / "deploy.sh"
        wrapper.write_text("#!/bin/sh\n./deploy.sh\n")
        deploy.write_text("#!/bin/sh\nhermes gateway stop\n")

        assert contains_gateway_lifecycle_command_or_referenced_script(
            "./wrapper.sh", cwd=str(tmp_path)
        ) is True

    def test_bare_python_heredoc_masks_data_but_still_reads_named_scripts(self, tmp_path):
        """A >1 MiB data file named inside a bare ``python3 <<PY`` body is Python data, never a
        shell script to fail closed on; a lifecycle script the body hands to ``os.system`` is still
        read through the masked-view mention pass (#113944), so masking cannot hide it."""
        from cron.lifecycle_guard import scan_gateway_lifecycle

        big = tmp_path / "big_blob.txt"
        big.write_text("x" * (2 * 1024 * 1024))
        script = tmp_path / "restart.sh"
        script.write_text("#!/bin/sh\nhermes gateway restart\n")

        data_command = f"python3 <<PY\nprint(open('{big}').read(10))\nPY\n"
        script_command = f"python3 <<PY\nimport os\nos.system('{script}')\nPY\n"
        assert scan_gateway_lifecycle(data_command, cwd=str(tmp_path)) == (False, None)
        assert scan_gateway_lifecycle(script_command, cwd=str(tmp_path)) == (True, None)

    @pytest.mark.parametrize("command", [
        # The masked payload keeps its quotes, so the rest of its line still tokenizes.
        "python3 -c 'print(1)'; pkill python",
        'python3 -c "print(1)" && pkill -9 python3',
        "python3 -c 'print(1)'; taskkill /F /IM python.exe",
        "python3 -c 'print(1)'; kill $(pidof python3)",
        "python3 -c 'print(1)'; hermes gate\"way\" restart",
        "python3 -c 'print(1)'; launchctl kick\"start\" -k gui/501/ai.hermes.gateway",
        "python3 -c 'print(1)'; bash -c 'hermes gate\"way\" restart'",
        # `python3 -c '` that the shell reads as quoted data, a comment or an argument.
        "echo \"run python3 -c '\"; hermes gateway restart; echo \"'\"",
        "echo hi # python3 -c '\nhermes gateway restart\necho done # '",
        "echo python3 -c '; hermes gateway restart' | bash",
        "echo $(date) python3 -c '; hermes gateway restart' | bash",
        "su python3 -c '; hermes gateway restart'",
        "x=(python3 -c '; hermes gateway restart'); eval \"${x[@]}\"",
        "python3() { bash \"$@\"; }; python3 -c 'hermes gateway restart'",
        "function python3 { bash \"$@\"; }; python3 -c 'hermes gateway restart'",
        # Payload ends follow the shell's quoting rules.
        "echo $'\\'; python3 -c '; hermes gateway restart; echo 'x'",
        "python3 -c \"x\\\" python3 -c '\" ; hermes gateway restart ; echo '\"'",
        "python3 -c 'python3 -c \"x' ; hermes gateway restart ; echo \"'\"",
        # The shell runs command substitutions before Python starts.
        "python3 -c \"print('$(hermes gateway restart)')\"",
        "python3 <<PY\nprint(\"$(hermes gateway restart)\")\nPY\n",
        # A python heredoc opener inside a quoted string opens no heredoc.
        "echo \"\npython3 <<PY\n\" ; hermes gateway restart ; echo \"\nPY\n\"",
        "su python3 <<PY\nhermes gateway restart\nPY\n",
        # The direct scan reads Python payloads unmasked: os.system() runs them in a shell (#127698).
        "python3 -c 'import os; os.system(\"hermes gateway restart\")'",
        "FOO=1 python3 -c \"import os; os.system('hermes gateway stop')\"",
        "python3 <<PY\nimport os\nos.system('launchctl bootout gui/501/ai.hermes.gateway')\nPY\n",
        "python3 << 'EOF'\nimport os\nmessage = \"hermes gateway restart\"\nos.system(message)\nEOF\n",
        # Branch E (#113667) still reads Python argv lists in a python heredoc body.
        "python3 <<PY\nimport subprocess\nsubprocess.run([\"pkill\", \"-f\", \"python\"])\nPY\n",
        "python3 <<PY\nimport subprocess\nsubprocess.run([\"taskkill\", \"/IM\", \"python.exe\", \"/F\"])\nPY\n",
    ])
    def test_shell_outside_proven_python_payloads_stays_blocked(self, tmp_path, command):
        """Only a payload the shell lexer proves is Python is masked; every shell command around
        it, or in text that merely looks like a payload, is still scanned."""
        from cron.lifecycle_guard import scan_gateway_lifecycle

        assert scan_gateway_lifecycle(command, cwd=str(tmp_path)) == (True, None)

    def test_script_after_python_payload_is_still_walked(self, tmp_path):
        from cron.lifecycle_guard import scan_gateway_lifecycle

        big = tmp_path / "big.sh"
        big.write_text("#" * (2 * 1024 * 1024))

        unsafe, refusal = scan_gateway_lifecycle(f"python3 -c 'print(1)'; {big}", cwd=str(tmp_path))
        assert unsafe is True
        assert refusal is not None and "scan cap" in refusal

    @pytest.mark.parametrize("command", [
        "cd /tmp && FOO=1 python3 <<PY\nprint(open('{big}').read(1))\nPY\n",
        "sudo -u bob python3 -u <<PY\nprint(open('{big}').read(1))\nPY\n",
        "if python3 <<PY\nprint(open('{big}').read(1))\nPY\nthen echo ok; fi\n",
        "x=$(python3 <<PY\nprint(open('{big}').read(1))\nPY\n)\n",
        "python3 -c 'print(1)'\npython3 - <<PY\nprint(open('{big}').read(1))\nPY\n",
        "cd /tmp && timeout 5 python3 <<PY\nprint(open('{big}').read(1))\nPY\n",
    ])
    def test_python_payload_text_is_masked_in_every_command_position(self, tmp_path, command):
        """A Python heredoc body after env assignments, wrappers, reserved words, list operators
        and inside `$(...)` is still interpreter data for the executed walk: a >1 MiB data file
        it opens is not a shell script to fail closed on."""
        from cron.lifecycle_guard import scan_gateway_lifecycle

        big = tmp_path / "big_blob.txt"
        big.write_text("x" * (2 * 1024 * 1024))

        assert scan_gateway_lifecycle(command.format(big=big), cwd=str(tmp_path)) == (False, None)
