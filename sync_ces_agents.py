import json
import os
import subprocess
import urllib.request


def sync_agents():
    print("Authenticating via gcloud...")
    token_cmd = "gcloud auth print-access-token 2>/dev/null || gcloud auth application-default print-access-token 2>/dev/null"
    token = subprocess.check_output(token_cmd, shell=True).decode("utf-8").strip()

    project_id = os.getenv("PROJECT_ID", "your-gcp-project-id")
    app_id = os.getenv("CES_APP_ID", "f65b3a44-7067-4a78-8d7e-3e3ebefdcfd3")
    location = os.getenv("CES_LOCATION", "us")
    app_name = f"projects/{project_id}/locations/{location}/apps/{app_id}"

    def get_agents():
        url = f"https://ces.googleapis.com/v1/{app_name}/agents"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode()).get("agents", [])

    def update_agent_instruction(agent_name, new_instruction):
        url = f"https://ces.googleapis.com/v1/{agent_name}?updateMask=instruction"
        payload = json.dumps({"instruction": new_instruction}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="PATCH")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as _:
            print(f"Updated {agent_name} successfully.")

    def read_file(filepath):
        with open(filepath) as f:
            return f.read()

    print("Fetching existing agents from CES CX API...")
    agents = get_agents()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    agents_dir = os.path.join(script_dir, "app", "agents")
    mapping = {
        "OmniRetail AI Receptionist": os.path.join(agents_dir, "router.txt"),
        "FAQ Specialist": os.path.join(agents_dir, "faq_receptionist.txt"),
        "WISMO Specialist": os.path.join(agents_dir, "wismo_receptionist.txt"),
        "After Hours Specialist": os.path.join(agents_dir, "receptionist.txt"),
        "Exit Specialist": os.path.join(agents_dir, "exit_agent.txt"),
    }

    for agent in agents:
        display_name = agent["displayName"]
        name = agent["name"]

        if display_name in mapping:
            path = mapping[display_name]
            if os.path.exists(path):
                new_inst = read_file(path)
                print(
                    f"Updating {display_name} with contents of {os.path.basename(path)}..."
                )
                update_agent_instruction(name, new_inst)
            else:
                print(f"File {path} not found for {display_name}")

    def create_and_deploy_version():
        # 1. Get latest version
        url = f"https://ces.googleapis.com/v1/{app_name}/versions?pageSize=5"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req) as response:
                versions = json.loads(response.read().decode()).get("appVersions", [])
            if versions:
                latest_display = versions[0]["displayName"]
                if latest_display.startswith("v"):
                    try:
                        num = float(latest_display[1:])
                        next_display = f"v{num + 0.1:.1f}"
                    except ValueError:
                        next_display = f"{latest_display}-updated"
                else:
                    next_display = f"{latest_display}-updated"
            else:
                next_display = "v1.0"
        except Exception as e:
            print(f"Failed to fetch versions: {e}")
            next_display = "v-auto-updated"

        # 2. Create the version
        print(f"Creating new App version: {next_display}...")
        create_url = f"https://ces.googleapis.com/v1/{app_name}/versions"
        create_payload = json.dumps({"displayName": next_display}).encode("utf-8")
        create_req = urllib.request.Request(create_url, data=create_payload, method="POST")
        create_req.add_header("Authorization", f"Bearer {token}")
        create_req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(create_req) as response:
                res = json.loads(response.read().decode())
                new_version_name = res["name"]
                print(f"Created version successfully: {new_version_name}")
        except Exception as e:
            print(f"Failed to create version: {e}")
            return

        # 3. Patch the deployment
        deployment_id = "a4c4f2b5-f3ca-47b9-ac95-18d03f0091a9"
        deploy_url = f"https://ces.googleapis.com/v1/{app_name}/deployments/{deployment_id}?updateMask=appVersion"
        deploy_payload = json.dumps({"appVersion": new_version_name}).encode("utf-8")
        deploy_req = urllib.request.Request(deploy_url, data=deploy_payload, method="PATCH")
        deploy_req.add_header("Authorization", f"Bearer {token}")
        deploy_req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(deploy_req) as response:
                print("Successfully updated live deployment to use the new version!")
        except Exception as e:
            print(f"Failed to update deployment: {e}")

    create_and_deploy_version()
    print("Done sync.")


if __name__ == "__main__":
    sync_agents()
