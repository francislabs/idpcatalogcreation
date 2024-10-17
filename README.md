# idpcatalogcreation
Bulk create python script to create catalog.yaml for the github app

#To use this export the below variables in your environment

export GITHUB_APP_ID="appid"
export GITHUB_INSTALLATION_ID="installationid" #this is not alphanumeric click on your url of app installation to find it
export GITHUB_PRIVATE_KEY_PATH="pathto.private-key.pem" #pem file for the app


ex: python3 gitapp.py --org fmlabsindia  --create-yamls
