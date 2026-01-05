# **VKS PVC Audit Script**

This script (vks\_disk\_audit.py) audits Kubernetes Persistent Volume Claims (PVCs) within a specific vSphere Supervisor Namespace. It correlates Kubernetes objects with underlying vSphere Cloud Native Storage (CNS) volumes to provide a comprehensive view of storage consumption across all Guest Clusters in that namespace.

## **Features**

* **Cluster Mapping:** Automatically identifies which Guest Cluster a PVC belongs to by analyzing OwnerReferences (VSphereMachines) and specific cluster labels.  
* **Storage Insights:** Uses govc to retrieve backend vSphere details:  
  * Datastore location  
  * Physical capacity usage  
  * Health status  
* **Referred Entity Resolution:** Decodes CNS metadata to show exactly which Pod or Guest Cluster PVC is consuming the volume, filtering out Supervisor-level noise.  
* **Categorized Output:** Separates volumes into:  
  1. **Node Volumes (Attached):** Volumes currently mounted to a specific Worker Node(containerd volumes etc.).  
  2. **In-Cluster PVCs:** Volumes associated with a cluster due to the volumes being PVCs used by pods in the cluster. 
* **JSON Support:** Optional JSON output for programmatic parsing and integration with other tools.

## **Prerequisites**

The machine running this script must have the following tools installed and available in the system $PATH:

1. **Python 3**  
2. **kubectl**: Configured with context pointing to the Supervisor Cluster.  
3. **govc**: The vSphere CLI tool.

### **Environment Configuration**

You must set the standard govc environment variables to allow the script to authenticate with vCenter:

export GOVC\_URL="vcenter.example.com"  
export GOVC\_USERNAME="administrator@vsphere.local"  
export GOVC\_PASSWORD="your-password"  
export GOVC\_INSECURE=1  \# Optional: If using self-signed certs

## **Usage**

### **1\. Basic Audit**

Run the script by providing the **Supervisor Namespace** you want to audit.

python3 vks\_disk\_audit.py \<SUPERVISOR\_NAMESPACE\>

**Example:**

python3 vks\_disk\_audit.py development-ns

**Sample Output:**

```text
=======================================================================================
                             NODE VOLUMES (Attached)
=======================================================================================
PVC Name                        Node                            Cluster              Volume Name                          Volume ID                                Datastore            Capacity   Referred Entity
------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
tfd-1-8q26s-ql69w-vol-b9xl      tfd-1-8q26s-ql69w               tfd-1                pvc-560fd41e-f243-4c96-997e...       560fd41e-f243-4c96-997e-8bf7b7996e95     vsan:8740804e67...   20.00 GB   -
tfd-1-tfd-1-jrxdt-pmhmn...      tfd-1-tfd-1-jrxdt-pmhmn...      tfd-1                pvc-ecfffa9d-483c-4fe1-a391...       ecfffa9d-483c-4fe1-a391-e4fe1986e52d     vsan:8740804e67...   20.00 GB   -


=======================================================================================
                             IN-CLUSTER PVCs
=======================================================================================
PVC Name                        Cluster              Volume Name                          Volume ID                                Datastore            Capacity   Referred Entity
----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
f478b761-3832-4c56-8cc7...      tfd-1                pvc-2b2deeab-53cf-4551-bba4...       2b2deeab-53cf-4551-bba4-e787dc1567d8     vsan:8740804e67...   1.00 GB    PVC:music-store/order-pvc, Pod:order-service-69987cbbfd-mlj2c
f478b761-3832-4c56-8cc7...      tfd-1                pvc-b2c6ac20-b002-4f0a-948b...       b2c6ac20-b002-4f0a-948b-4fccef7d8c3a     vsan:8740804e67...   1.00 GB    PVC:music-store/cart-pvc, Pod:cart-service-7cc8794c86-x2m6v
f478b761-3832-4c56-8cc7...      tfd-1                pvc-5fd5d74c-2b77-4ecb-ae95...       5fd5d74c-2b77-4ecb-ae95-c4a22ae99111     vsan:8740804e67...   1.00 GB    Pod:postgres-bcf8997c4-89pkj, PVC:music-store/postgres-pvc
f478b761-3832-4c56-8cc7...      tfd-1                pvc-6fe37d12-9c30-4ab5-aaf7...       6fe37d12-9c30-4ab5-aaf7-7f9276e3ba49     vsan:8740804e67...   1.00 GB    PVC:music-store/music-store-1-pvc
f478b761-3832-4c56-8cc7...      tfd-1                pvc-0f28021c-c85c-4f36-b9de...       0f28021c-c85c-4f36-b9de-faa26fedb232     vsan:8740804e67...   1.00 GB    PVC:music-store/users-pvc, Pod:users-service-855678b958-9zcdb
```


### **2\. JSON Output**

Use the \--json flag to output pure JSON. This is useful for piping into jq or other automation scripts.

python3 vks\_disk\_audit.py \<SUPERVISOR\_NAMESPACE\> \--json

**Example:**

python3 vks\_disk\_audit.py development-ns \--json \> audit\_report.json

**JSON Structure:**

```json
{
  "node_volumes": [
    {
      "pvc_name": "tfd-1-8q26s-ql69w-vol-b9xl",
      "cluster": "tfd-1",
      "node": "tfd-1-8q26s-ql69w",
      "volume_name": "pvc-560fd41e-f243-4c96-997e-8bf7b7996e95",
      "volume_handle": "560fd41e...",
      "datastore": "vsan:8740804e67...",
      "capacity": "20.00 GB",
      "referred_entity": "-"
    }
  ],
  "cluster_pvcs": [ ... ]
}
```


## **Troubleshooting**

* **"Error: 'govc' is not installed":** Ensure you have downloaded govc and moved it to /usr/local/bin or another directory in your path.  
* **Empty Output / No PVCs Found:**  
  * Verify you are logged into the correct Supervisor Cluster context via kubectl.  
  * Ensure the namespace provided actually contains PVCs.  
  * Check if GOVC\_URL is pointing to the correct vCenter server that manages the Supervisor Cluster.  
* **"Unattached/Unknown" Cluster:** If the script cannot identify the cluster, check if the PVCs are legacy volumes or if the Guest Cluster uses non-standard naming conventions (labels other than cluster.x-k8s.io/cluster-name or \*/TKGService).