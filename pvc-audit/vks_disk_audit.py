#!/usr/bin/env python3

import subprocess
import json
import sys
import os
import shutil

# ==============================================================================
# Script Name: vks_disk_audit.py
# Description: Maps Kubernetes PVs in a Supervisor Namespace to vSphere CNS Volumes
#              using 'kubectl' and 'govc' via subprocess.
#              Audits ALL clusters in the provided namespace.
#              Separates output by Node-Attached volumes and General Cluster PVCs.
#              Supports JSON output via --json flag.
# Requirements: python3, kubectl, govc
# ==============================================================================

def check_dependencies():
    """Ensures required tools are installed."""
    for tool in ["kubectl", "govc"]:
        if shutil.which(tool) is None:
            print(f"Error: '{tool}' is not installed or not in PATH.", file=sys.stderr)
            sys.exit(1)

def run_command(cmd_list):
    """Helper to run shell commands and return stdout."""
    try:
        result = subprocess.run(
            cmd_list,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        # Don't exit immediately on simple errors, let caller handle or return empty
        return None

def main():
    # Argument Parsing
    json_output = False
    args = sys.argv[1:]
    if "--json" in args:
        json_output = True
        args.remove("--json")

    if len(args) != 1:
        print(f"Usage: python3 {sys.argv[0]} <supervisor_namespace> [--json]", file=sys.stderr)
        print(f"Example: python3 {sys.argv[0]} development-ns --json", file=sys.stderr)
        sys.exit(1)

    namespace = args[0]

    # Helper for logging (suppress in JSON mode)
    def log(msg):
        if not json_output:
            print(msg)

    # Validate Environment
    check_dependencies()
    if not os.environ.get("GOVC_URL"):
        log("Warning: GOVC_URL is not set. Ensure govc is configured.")

    log(f"--- Starting Audit for Namespace: {namespace} ---")

    # 1. Build a Map of Node UIDs to Cluster Names AND Node Names
    log("Mapping VSphereMachines to Clusters...")
    vm_cmd = [
        "kubectl", "get", "vspheremachine",
        "-n", namespace,
        "-o", "json"
    ]
    
    vm_json_raw = run_command(vm_cmd)
    node_info = {} # UID -> {'cluster': cluster_name, 'name': node_name}

    if vm_json_raw:
        try:
            vm_data = json.loads(vm_json_raw)
            for vm in vm_data.get("items", []):
                uid = vm["metadata"].get("uid")
                name = vm["metadata"]["name"]
                labels = vm["metadata"].get("labels", {})
                # CAPI Cluster Name Label
                cluster_name = labels.get("cluster.x-k8s.io/cluster-name", "Unknown")
                if uid:
                    node_info[uid] = {
                        "cluster": cluster_name,
                        "name": name
                    }
        except json.JSONDecodeError:
            log("Error parsing VSphereMachine JSON. Cluster association may fail.")

    log(f"Mapped {len(node_info)} nodes across clusters.")

    # 2. Get ALL PVCs in namespace
    log("Querying all PVCs in namespace...")
    pvc_cmd = [
        "kubectl", "get", "pvc",
        "-n", namespace,
        "-o", "json"
    ]
    
    pvc_json_raw = run_command(pvc_cmd)
    if not pvc_json_raw:
        log("Error querying PVCs.")
        sys.exit(1)

    try:
        pvc_data = json.loads(pvc_json_raw)
    except json.JSONDecodeError:
        log("Error parsing PVC JSON.")
        sys.exit(1)

    all_pvcs = pvc_data.get("items", [])
    
    if not all_pvcs:
        log(f"No PVCs found in namespace '{namespace}'.")
        if json_output:
            print(json.dumps({"node_volumes": [], "cluster_pvcs": []}))
        sys.exit(0)

    log(f"Found {len(all_pvcs)} PVC(s). Resolving PV handles...")

    # 3. Extract Volume Handles and Separate Lists
    node_volumes = []
    cluster_pvcs = []
    volume_ids = []

    for pvc in all_pvcs:
        pvc_name = pvc["metadata"]["name"]
        pv_name = pvc["spec"].get("volumeName")
        
        cluster_assoc = "Unattached/Unknown"
        node_assoc = "-"
        is_node_attached = False
        
        owner_refs = pvc.get("metadata", {}).get("ownerReferences", [])
        
        # 3a. Try to associate via VSphereMachine OwnerReference
        for ref in owner_refs:
            if ref.get("uid") in node_info:
                info = node_info[ref.get("uid")]
                cluster_assoc = info["cluster"]
                node_assoc = info["name"]
                is_node_attached = True
                break
        
        # 3b. Fallback: Check Labels if not attached
        if not is_node_attached:
            labels = pvc.get("metadata", {}).get("labels", {})
            # Priority Check: Look for dynamic key ending in /TKGService
            for key in labels.keys():
                if key.endswith("/TKGService"):
                    cluster_assoc = key.split("/TKGService")[0]
                    break
            
            # Secondary Check: Standard CAPI label
            if cluster_assoc == "Unattached/Unknown" and "cluster.x-k8s.io/cluster-name" in labels:
                cluster_assoc = labels["cluster.x-k8s.io/cluster-name"]

        # Prepare Entry object
        entry = {
            "pvc_name": pvc_name,
            "cluster": cluster_assoc,
            "node": node_assoc,
            "volume_handle": None
        }

        if not pv_name:
            # Record unbound PVCs
            entry["volume_handle"] = None
        else:
            # Fetch PV details to get the CSI Handle
            pv_cmd = ["kubectl", "get", "pv", pv_name, "-o", "json"]
            pv_raw = run_command(pv_cmd)
            
            handle = None
            if pv_raw:
                try:
                    pv_data = json.loads(pv_raw)
                    csi = pv_data["spec"].get("csi")
                    if csi:
                        handle = csi.get("volumeHandle")
                except:
                    pass

            if handle:
                entry["volume_handle"] = handle
                volume_ids.append(handle)
            else:
                entry["volume_handle"] = "Not CSI/Found"

        # Sort into appropriate list
        if is_node_attached:
            node_volumes.append(entry)
        else:
            cluster_pvcs.append(entry)

    # 4. Query govc (Batch Mode)
    cns_data_map = {}
    if volume_ids:
        log(f"Querying vSphere CNS for {len(volume_ids)} volumes...")
        govc_cmd = ["govc", "volume.ls", "-json"] + volume_ids
        govc_raw = run_command(govc_cmd)

        if govc_raw:
            try:
                govc_output = json.loads(govc_raw)
                # Fallback keys for different govc versions
                volumes = govc_output.get("volume", govc_output.get("Volumes", []))
                
                for vol in volumes:
                    vol_id = None
                    if "volumeId" in vol and "id" in vol["volumeId"]:
                        vol_id = vol["volumeId"]["id"]
                    elif "VolumeId" in vol and "Id" in vol["VolumeId"]:
                        vol_id = vol["VolumeId"]["Id"]
                    
                    if vol_id:
                        cns_data_map[vol_id] = vol

            except json.JSONDecodeError:
                log("Error parsing govc output.")

    # 5. Data Enrichment Function
    def enrich_entry(entry, cns_data_map):
        handle = entry["volume_handle"]
        
        ds_name = "Unknown"
        vol_name = "-"
        capacity_str = "-"
        referred_entity_str = "-"

        if handle and handle in cns_data_map:
            vol_info = cns_data_map[handle]
            
            # Volume Name
            vol_name = vol_info.get("name", vol_info.get("Name", "-"))

            # Datastore
            if "datastoreUrl" in vol_info:
                raw_ds = vol_info["datastoreUrl"]
                if raw_ds.startswith("ds:///vmfs/volumes/"):
                    ds_name = raw_ds.replace("ds:///vmfs/volumes/", "")
                else:
                    ds_name = raw_ds
            elif "Datastore" in vol_info and vol_info["Datastore"]:
                ds_name = vol_info["Datastore"].get("Name", "Unknown")
            
            # Capacity
            backing = vol_info.get("backingObjectDetails", vol_info.get("BackingObjectDetails", {}))
            cap_mb = backing.get("capacityInMb", backing.get("CapacityInMB", 0))
            if cap_mb > 0:
                capacity_str = f"{cap_mb / 1024:.2f} GB"

            # Referred Entity Logic
            metadata = vol_info.get("metadata", {})
            entity_metadata_list = metadata.get("entityMetadata", [])
            
            refs = []
            for em in entity_metadata_list:
                # Filter out Supervisor references to keep output clean for Guest Cluster focus
                e_cluster = em.get("clusterID", "")
                if "vspheresupervisor" in e_cluster.lower():
                    continue

                e_type = em.get("entityType", "")
                e_name = em.get("entityName", "")
                
                if e_type == "POD":
                    refs.append(f"Pod:{e_name}")
                elif e_type == "PERSISTENT_VOLUME_CLAIM":
                    e_ns = em.get("namespace", "")
                    if e_ns:
                        refs.append(f"PVC:{e_ns}/{e_name}")
                    else:
                        refs.append(f"PVC:{e_name}")
            
            if refs:
                referred_entity_str = ", ".join(refs)
        
        entry["volume_name"] = vol_name
        entry["datastore"] = ds_name
        entry["capacity"] = capacity_str
        entry["referred_entity"] = referred_entity_str
        return entry

    # Process lists with enrichment
    processed_node_volumes = [enrich_entry(e, cns_data_map) for e in node_volumes]
    processed_cluster_pvcs = [enrich_entry(e, cns_data_map) for e in cluster_pvcs]

    # 6. Output Generation
    if json_output:
        output_data = {
            "node_volumes": processed_node_volumes,
            "cluster_pvcs": processed_cluster_pvcs
        }
        print(json.dumps(output_data, indent=2))
        sys.exit(0)

    # Table Printing Helper
    def print_table(data_list, include_node=False):
        # Updated format strings to include Volume Name
        
        if include_node:
            # Columns: PVC Name, Node, Cluster, Volume Name, Volume ID, Datastore, Capacity, Referred Entity
            header_fmt = "{:<30} {:<30} {:<20} {:<35} {:<40} {:<20} {:<10} {}"
            row_fmt = "{:<30} {:<30} {:<20} {:<35} {:<40} {:<20} {:<10} {}"
            
            print(header_fmt.format("PVC Name", "Node", "Cluster", "Volume Name", "Volume ID", "Datastore", "Capacity", "Referred Entity"))
            print("-" * 210)
        else:
            # Columns: PVC Name, Cluster, Volume Name, Volume ID, Datastore, Capacity, Referred Entity
            header_fmt = "{:<30} {:<20} {:<35} {:<40} {:<20} {:<10} {}"
            row_fmt = "{:<30} {:<20} {:<35} {:<40} {:<20} {:<10} {}"
            
            print(header_fmt.format("PVC Name", "Cluster", "Volume Name", "Volume ID", "Datastore", "Capacity", "Referred Entity"))
            print("-" * 180)

        for item in data_list:
            display_handle = item["volume_handle"] if item["volume_handle"] else "N/A"
            vol_name = item["volume_name"]
            
            # Truncation logic removed as requested

            if include_node:
                print(row_fmt.format(
                    item["pvc_name"], item["node"], item["cluster"], vol_name, display_handle, 
                    item["datastore"], item["capacity"], item["referred_entity"]
                ))
            else:
                print(row_fmt.format(
                    item["pvc_name"], item["cluster"], vol_name, display_handle, 
                    item["datastore"], item["capacity"], item["referred_entity"]
                ))

    print("\n")
    print("=======================================================================================")
    print("                             NODE VOLUMES (Attached)")
    print("=======================================================================================")
    if processed_node_volumes:
        print_table(processed_node_volumes, include_node=True)
    else:
        print("(No volumes currently attached to nodes found)")

    print("\n")
    print("=======================================================================================")
    print("                             IN-CLUSTER PVCs")
    print("=======================================================================================")
    if processed_cluster_pvcs:
        print_table(processed_cluster_pvcs, include_node=False)
    else:
        print("(No in-cluster PVCs found)")

    log("\n--- Audit Complete ---")

if __name__ == "__main__":
    main()