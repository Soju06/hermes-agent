"""Python ``-c`` sources and Python heredoc bodies are interpreter data, not shell source.

The lifecycle guard must not shell-tokenize them (a ``sys.path.insert(0, '<dir>')`` line read as a
referenced script, a lifecycle-looking Python string read as a command), while every real shell
lifecycle command around them stays blocked.
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

    def test_python_heredoc_lifecycle_string_not_blocked(self, tmp_path):
        """Lifecycle-looking Python string data is not an executed command."""
        from cron.lifecycle_guard import (
            contains_gateway_lifecycle_command_or_referenced_script,
        )
        command = """python3 << 'EOF'
message = "hermes gateway restart"
print(message)
EOF
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
