#!/usr/bin/env bash
# Read-only Docker runtime and host-capacity inspection.
set -uo pipefail
# Inspect the Node's system-default Docker context, never a caller-selected endpoint.
unset DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH
export DOCKER_HOST=unix:///var/run/docker.sock

started_ns=$(date +%s%N 2>/dev/null || echo 0)
observed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
diagnostics='[]'
add_diag() {
    local code="$1" severity="$2" message="$3"
    diagnostics=$(python3 -c 'import json,sys; a=json.loads(sys.argv[1]); a.append({"code":sys.argv[2],"severity":sys.argv[3],"message":sys.argv[4]}); print(json.dumps(a,separators=(",",":")))' "$diagnostics" "$code" "$severity" "$message")
}

cpu=$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)
[[ "$cpu" =~ ^[1-9][0-9]*$ ]] || cpu=null
mem_total=$(awk '/MemTotal:/ {print $2 * 1024}' /proc/meminfo 2>/dev/null || true)
mem_available=$(awk '/MemAvailable:/ {print $2 * 1024}' /proc/meminfo 2>/dev/null || true)
[[ "$mem_total" =~ ^[0-9]+$ ]] || mem_total=null
[[ "$mem_available" =~ ^[0-9]+$ ]] || mem_available=null

docker_path=$(command -v docker 2>/dev/null || true)
docker_installed=false
client_version=null
server_version=null
api_version=null
minimum_api_version=null
engine_availability="Not Installed"
compose_installed=false
compose_version=null
compose_compatibility="Unavailable"
storage_driver=null
data_root=null
mount_point=null
fs_total=null
fs_available=null
fs_used=null
execution_identity=$(id -un 2>/dev/null || true)
[[ -n "$execution_identity" ]] || execution_identity=null
inspection_access="Unknown"

# Host storage is a pre-install requirement. Measure the filesystem that will
# contain Docker's default data root even when Docker is not installed yet.
storage_path="/"
if [[ -n "$docker_path" ]]; then
    docker_installed=true
    client_version=$(docker version --format '{{.Client.Version}}' 2>/dev/null || true)
    [[ -n "$client_version" ]] || client_version=null
    compose_version=$(docker compose version --short 2>/dev/null || true)
    if [[ -n "$compose_version" ]]; then
        compose_installed=true
        compose_compatibility="Compatible"
    else
        compose_compatibility="Unavailable"
        add_diag "COMPOSE_UNAVAILABLE" "Warning" "Docker Compose v2 plugin is unavailable."
    fi
    info=$(docker info --format '{{json .}}' 2>&1 || true)
    if python3 -c 'import json,sys; json.loads(sys.stdin.read())' <<< "$info" 2>/dev/null; then
        engine_availability="Available"
        inspection_access="Allowed"
        server_version=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("ServerVersion") or "")' "$info" 2>/dev/null || true)
        api_version=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("ServerAPIVersion") or "")' "$info" 2>/dev/null || true)
        minimum_api_version=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("MinAPIVersion") or "")' "$info" 2>/dev/null || true)
        storage_driver=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("Driver") or "")' "$info" 2>/dev/null || true)
        data_root=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("DockerRootDir") or "")' "$info" 2>/dev/null || true)
        if [[ -n "$data_root" && -d "$data_root" ]]; then
            storage_path="$data_root"
        fi
    else
        engine_availability="Unavailable"
        if grep -qiE 'permission denied|permission.*socket' <<< "$info"; then
            inspection_access="Denied"
            add_diag "DOCKER_ACCESS_DENIED" "Error" "SSH identity cannot access the Docker Engine socket; no privilege escalation was attempted."
        else
            inspection_access="Unknown"
            add_diag "DOCKER_ENGINE_UNAVAILABLE" "Error" "Docker CLI is present but the Engine daemon is unavailable."
        fi
    fi
else
    add_diag "DOCKER_CLI_MISSING" "Error" "Docker CLI is not installed or is not available in PATH."
fi

# Use Docker's data-root filesystem when it exists; otherwise use the root
# filesystem as the pre-install baseline for the default /var/lib/docker path.
read -r mount_point fs_total fs_used fs_available < <(df -B1 --output=target,size,used,avail "$storage_path" 2>/dev/null | tail -n 1)

for var in server_version api_version minimum_api_version storage_driver data_root mount_point; do
    [[ -n "${!var:-}" ]] || printf -v "$var" '%s' null
done
[[ "$fs_total" =~ ^[0-9]+$ ]] || fs_total=null
[[ "$fs_available" =~ ^[0-9]+$ ]] || fs_available=null
[[ "$fs_used" =~ ^[0-9]+$ ]] || fs_used=null

inspection_status="Successful"
if [[ "$cpu" == "null" || "$mem_total" == "null" || "$mem_available" == "null" ]]; then
    inspection_status="Partial"
    add_diag "HOST_CAPACITY_INCOMPLETE" "Warning" "One or more host CPU or memory measurements could not be collected."
fi
if [[ "$engine_availability" == "Available" && "$storage_driver" == "null" ]]; then
    inspection_status="Partial"
    add_diag "DOCKER_STORAGE_INCOMPLETE" "Warning" "Docker storage metadata could not be collected."
fi
if [[ "$fs_available" == "null" || "$fs_total" == "null" ]]; then
    inspection_status="Partial"
    add_diag "HOST_STORAGE_INCOMPLETE" "Warning" "Host filesystem capacity could not be measured."
fi

host_inventory_json=$(python3 "$(dirname "$0")/inspect_docker_host.py" 2>/dev/null || true)
if ! python3 -c 'import json,sys; json.loads(sys.stdin.read())' <<< "$host_inventory_json" 2>/dev/null; then
    host_inventory_json='{"schema_version":"1.0.0","observed_at":null,"host_os":{"id":null,"id_like":null,"name":null,"version_id":null,"version_codename":null,"pretty_name":null,"architecture":null,"kernel_release":null},"package_manager":"Unknown","docker_packages":[],"services":{"manager":"Unknown","docker":"Unknown","containerd":"Unknown"},"workloads":{"available":false,"running_containers":null,"total_containers":null}}'
    add_diag "HOST_INVENTORY_UNAVAILABLE" "Warning" "The Docker host inventory probe did not return a valid payload."
    inspection_status="Partial"
fi

finished_ns=$(date +%s%N 2>/dev/null || echo "$started_ns")
duration_ms=0
if [[ "$started_ns" =~ ^[0-9]+$ && "$finished_ns" =~ ^[0-9]+$ ]]; then
    duration_ms=$(( (finished_ns - started_ns) / 1000000 ))
fi

python3 - "$observed_at" "$duration_ms" "$inspection_status" "$cpu" "$mem_total" "$mem_available" "$docker_installed" "$docker_path" "$client_version" "$engine_availability" "$server_version" "$api_version" "$minimum_api_version" "$compose_installed" "$compose_version" "$compose_compatibility" "$storage_driver" "$data_root" "$mount_point" "$fs_total" "$fs_available" "$fs_used" "$execution_identity" "$inspection_access" "$diagnostics" "$host_inventory_json" <<'PY'
import json, sys
(v_at, duration, status, cpu, mem_total, mem_available,
 cli_installed, cli_path, cli_version, engine, server, api, min_api,
 compose_installed, compose_version, compose_compat, driver, data_root,
 mount, fs_total, fs_available, fs_used, identity, access, diagnostics, host_inventory_json) = sys.argv[1:]
def number(value):
    return None if value == "null" else int(value)
def nullable(value):
    return None if value in ("", "null") else value
snapshot = {
 "schema_version":"1.0.0", "observed_at":v_at, "duration_ms":int(duration),
 "inspection_status":status,
 "host":{"cpu":{"logical_cores":number(cpu)},
         "memory":{"total_bytes":number(mem_total),"available_bytes":number(mem_available)}},
 "docker_cli":{"installed":cli_installed == "true","path":nullable(cli_path),"client_version":nullable(cli_version)},
 "engine":{"availability":engine,"server_version":nullable(server),"api_version":nullable(api),"minimum_api_version":nullable(min_api)},
 "compose":{"installed":compose_installed == "true","version":nullable(compose_version),"compatibility":compose_compat},
 "storage":{"driver":nullable(driver),"data_root":nullable(data_root),
            "filesystem":{"mount_point":nullable(mount),"total_bytes":number(fs_total),"available_bytes":number(fs_available),"used_bytes":number(fs_used)}},
 "permissions":{"execution_identity":nullable(identity),"inspection_access":access},
 "diagnostics":json.loads(diagnostics),
 "host_inventory":json.loads(host_inventory_json)
}
print(json.dumps(snapshot, separators=(",",":")))
PY
