"""Render the narrowly scoped Docker helper sudoers rule."""
import re
import sys
HELPER_PATH = "/usr/local/lib/dt-master/docker/docker_lifecycle_helper.py"
USERNAME_PATTERN = re.compile(r"[a-z_][a-z0-9_-]{0,31}\$?\Z")
def render_policy(username):
    if not isinstance(username, str) or not USERNAME_PATTERN.fullmatch(username):
        raise ValueError("SSH username is not valid for a sudoers rule")
    return f'{username} ALL=(root) NOPASSWD: {HELPER_PATH} ""\n'
def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: docker_sudoers_policy.py <ssh-username>")
    try:
        sys.stdout.write(render_policy(sys.argv[1]))
    except ValueError as exc:
        sys.stderr.write(f"DOCKER_SUDOERS_POLICY_INVALID: {exc}\n")
        raise SystemExit(2)
if __name__ == "__main__":
    main()
