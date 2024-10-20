import argparse
import requests
import os
import re
import jwt  # For generating JWT tokens
import time
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from requests.auth import HTTPBasicAuth
from requests.packages.urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
import subprocess

# GitHub App Configurations (replace these with your own GitHub App details)
APP_ID = os.getenv("GITHUB_APP_ID")
INSTALLATION_ID = os.getenv("GITHUB_INSTALLATION_ID")
PRIVATE_KEY_PATH = os.getenv("GITHUB_PRIVATE_KEY_PATH")

current_directory = os.path.basename(os.getcwd())
branch = "main"

yaml_content_template = """
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: {repo_name}
  tags:
    - auto-generated
  annotations:
    backstage.io/source-location: url:{repo_path}
    github.com/project-slug: {project_slug}
spec:
  type: service
  lifecycle: experimental
  owner: Harness_Account_All_Users
  system: {orgName}
"""

def generate_jwt(app_id, private_key_path):
    print(f"Generating JWT for App ID: {app_id}")
    try:
        with open(private_key_path, "r") as key_file:
            private_key = serialization.load_pem_private_key(
                key_file.read().encode(), password=None, backend=default_backend()
            )
    except Exception as e:
        print(f"Error reading private key: {e}")
        exit(1)

    payload = {
        "iat": int(time.time()),
        "exp": int(time.time()) + (10 * 60),  # JWT expiration set to 10 minutes
        "iss": app_id,
    }

    try:
        jwt_token = jwt.encode(payload, private_key, algorithm="RS256")
        print("JWT successfully generated.")
    except Exception as e:
        print(f"Error generating JWT: {e}")
        exit(1)

    return jwt_token

def get_installation_token(jwt_token, installation_id):
    print(f"Fetching installation token for Installation ID: {installation_id}")
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github.v3+json",
    }

    response = requests.post(url, headers=headers)
    print(f"Installation token response: {response.status_code} {response.text}")
    if response.status_code == 201:
        return response.json()["token"]
    else:
        raise Exception(f"Failed to get installation access token: {response.status_code} {response.text}")

def get_repositories_api(organization, token, current_directory=None, repo_pattern=None, per_page=100):
    print(f"Fetching repositories for organization: {organization}")
    url = f"https://api.github.com/orgs/{organization}/repos"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    all_repos_info = []
    page = 1
    while True:
        params = {"page": page, "per_page": per_page}
        response = requests.get(url, headers=headers, params=params)
        print(f"GitHub API response (page {page}): {response.status_code}")
        if response.status_code != 200:
            print(f"Error: Unable to fetch repositories from page {page}.")
            break
        repos = response.json()
        if not repos:
            print(f"No repositories found on page {page}.")
            break

        for repo in repos:
            repo_name = repo['name'].lower()
            if repo_name == current_directory:
                continue
            repo_path = repo['html_url']
            if repo_pattern is None or re.match(repo_pattern, repo_name):
                all_repos_info.append({"name": repo_name, "html_url": repo_path})
                print(f"Repository found: {repo_name}")
        page += 1

    print(f"Total repositories fetched: {len(all_repos_info)}")
    return all_repos_info

def list_repositories(organization, token, repo_pattern=None):
    yaml_files_created = 0
    print(f"Listing repositories in organization: {organization}")

    repos = get_repositories_api(organization, token)
    for repo in repos:
        repo_name = repo['name'].lower()
        if repo_name == current_directory:
            continue
        repo_path = repo['html_url']
        if repo_pattern is None or re.match(repo_pattern, repo_name):
            print(f"Processing repository: {repo_name}")
            create_or_update_catalog_info(organization, repo_name, repo_path)
            yaml_files_created += 1
    print("----------")
    print(f"Total YAML files created or updated: {yaml_files_created}")

def create_or_update_catalog_info(organization, repo_name, repo_path):
    directory = f"services/{repo_name}"
    if not os.path.exists(directory):
        os.makedirs(directory)
    
    yaml_file_path = f"{directory}/catalog-info.yaml"

    content = yaml_content_template.format(repo_name=repo_name, repo_path=repo_path, orgName=organization, project_slug=organization+'/'+repo_name)

    if os.path.exists(yaml_file_path):
        print(f"Updating catalog-info.yaml for {repo_name}")
        with open(yaml_file_path, "w") as file:
            file.write(content)
    else:
        print(f"Creating catalog-info.yaml for {repo_name}")
        with open(yaml_file_path, "w") as file:
            file.write(content)

def register_yamls(organization, account, x_api_key):
    print("Registering YAML files...")
    count = 0
    api_url = f"https://idp.harness.io/{account}/idp/api/catalog/locations"

    repos = [name for name in os.listdir("./services") if os.path.isdir(os.path.join("./services", name))]
    for repo_name in repos:
        if repo_name != current_directory:
            directory = f"services/{repo_name}"
            api_payload = {
                "target": f"https://github.com/{organization}/{current_directory}/blob/{branch}/{directory}/catalog-info.yaml",
                "type": "url"
            }
            api_headers = {
                "x-api-key": f"{x_api_key}",
                "Content-Type": "application/json",
                "Harness-Account": f"{account}"
            }

            retries = Retry(total=3, backoff_factor=1, status_forcelist=[401, 500, 502, 503, 504])
            session = requests.Session()
            session.mount("http://", HTTPAdapter(max_retries=retries))
            session.mount("https://", HTTPAdapter(max_retries=retries))

            try:
                api_response = session.post(api_url, json=api_payload, headers=api_headers)
                print(f"Registration response for {repo_name}: {api_response.status_code}")
                if api_response.status_code == 200 or api_response.status_code == 201:
                    print(f"Location registered for file: {repo_name}")
                    count += 1
                elif api_response.status_code == 409:
                    refresh_payload = {
                        "entityRef": f"component:default/{repo_name}"
                    }
                    refresh_url = f"https://idp.harness.io/{account}/idp/api/catalog/refresh"
                    api_response = session.post(refresh_url, json=refresh_payload, headers=api_headers)
                    print(f"Location already exists for file: {repo_name}. Refreshing it")
                    count += 1
                else:
                    print(f"Failed to register location for file: {repo_name}. Status code: {api_response.status_code}")
            except requests.exceptions.RequestException as e:
                print(f"Failed to make API call for file: {repo_name}. Error: {str(e)}")

def push_yamls():
    print("Pushing YAMLs...")
    subprocess.run(["git", "add", "services/"])
    commit_message = "Adding YAMLs"
    subprocess.run(["git", "commit", "-m", commit_message])
    subprocess.run(["git", "push"])

def parse_arguments():
    parser = argparse.ArgumentParser(description="List repositories in a GitHub organization and manage catalog-info.yaml files")
    parser.add_argument("--org", help="GitHub organization name")
    parser.add_argument("--repo-pattern", help="Optional regex pattern to filter repositories")
    parser.add_argument("--create-yamls", action="store_true", help="Create or update catalog-info.yaml files")
    parser.add_argument("--register-yamls", action="store_true", help="Register existing catalog-info.yaml files")
    parser.add_argument("--run-all", action="store_true", help="Run all operations: create, register, and run")
    parser.add_argument("--x_api_key", help="Harness x-api-key")
    parser.add_argument("--account", help="Harness account")
    parser.add_argument("--branch", help="Your git branch")
    return parser.parse_args()

def main():
    args = parse_arguments()
    global branch
    if not (args.create_yamls or args.register_yamls or args.run_all):
        print("Error: One of --create-yamls, --register-yamls or --run_all must be used.")
        return
    if args.branch is not None:
        branch = args.branch

    print(f"App ID: {APP_ID}, Installation ID: {INSTALLATION_ID}, Private Key Path: {PRIVATE_KEY_PATH}")
    jwt_token = generate_jwt(APP_ID, PRIVATE_KEY_PATH)
    print(f"JWT Token: {jwt_token}")
    
    installation_token = get_installation_token(jwt_token, INSTALLATION_ID)
    print(f"Installation Token: {installation_token}")

    if args.create_yamls:
        if args.org is None:
            print("Provide GitHub org name using --org flag.")
            exit()
        list_repositories(args.org, installation_token, args.repo_pattern)
    elif args.register_yamls:
        if args.org is None or args.x_api_key is None or args.account is None:
            print("Provide GitHub org name, Harness account ID, and Harness x_api_key to create the YAMLs.")
            exit()
        register_yamls(args.org, args.account, args.x_api_key)

if __name__ == "__main__":
    main()
