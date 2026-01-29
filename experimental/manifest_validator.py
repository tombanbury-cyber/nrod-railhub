# manifest_validator.py
import fnmatch
import os
import yaml
import shlex
from pathlib import Path

class ManifestValidator:
    def __init__(self, manifest_path):
        self.manifest_path = Path(manifest_path)
        self.base = self.manifest_path.parent
        self.data = yaml.safe_load(self.manifest_path.read_text())
        self.allowed_paths = self.data.get("allowed_paths", [])
        self.deny_paths = self.data.get("deny_paths", [])
        self.allowed_commands = set(self.data.get("allowed_shell_commands", []))
        self.disallowed_commands = set(self.data.get("disallowed_shell_commands", []))
        self.git_policy = self.data.get("git", {})

    def _normalize(self, p: str) -> str:
        # return path relative to repo root (or manifest parent)
        rp = Path(p)
        if rp.is_absolute():
            try:
                return str(rp.relative_to(self.base.parent))
            except Exception:
                return str(rp)
        return str(Path(p))

    def is_path_allowed(self, path: str) -> bool:
        rel = self._normalize(path)
        # check allowlist first
        for pattern in self.allowed_paths:
            if fnmatch.fnmatch(rel, pattern):
                return True
        # otherwise denied
        for pattern in self.deny_paths:
            if fnmatch.fnmatch(rel, pattern):
                return False
        # default deny if not explicitly allowed
        return False

    def is_command_allowed(self, cmd: str) -> bool:
        # simple check: verify binary is in allowed list and not in disallowed
        # split safely
        parts = shlex.split(cmd) if isinstance(cmd, str) else cmd
        if len(parts) == 0:
            return False
        binary = os.path.basename(parts[0])
        if binary in self.disallowed_commands:
            return False
        if self.allowed_commands and binary not in self.allowed_commands:
            return False
        return True

    def is_git_push_allowed(self, branch: str, changed_paths: list) -> (bool, str):
        prefixes = self.git_policy.get("allowed_branches_prefix", [])
        commit_prefix = self.git_policy.get("commit_message_prefix", "")
        # branch check
        if prefixes:
            ok_branch = any(branch.startswith(p) for p in prefixes)
            if not ok_branch:
                return False, f"branch '{branch}' not in allowed prefixes {prefixes}"
        # paths check
        for p in changed_paths:
            if not self.is_path_allowed(p):
                return False, f"path '{p}' is not allowed by manifest"
        return True, "ok"

# Example usage:
if __name__ == "__main__":
    import sys
    mv = ManifestValidator("experimental/AGENT_MANIFEST.yaml")
    # quick smoke tests
    tests = [
        ("experimental/some_file.py", mv.is_path_allowed("experimental/some_file.py")),
        ("README.md", mv.is_path_allowed("README.md")),
    ]
    print(tests)
    print("cmd ok:", mv.is_command_allowed("python -m pytest"))
