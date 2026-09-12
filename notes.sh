# login to GCP 

#login
gcloud auth login 

#Terraform and then my screen name is admin@shanebaccas.altostrat.com 
gcloud auth application-default login --no-launch-browser


#get an agent token (you will have to add the service account to cloud run first!) 
gcloud auth print-identity-token


#Set the proj 
gcloud config set project proj1-487921