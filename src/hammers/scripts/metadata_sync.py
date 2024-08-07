import openstack
from blazarclient.client import Client as blazar_client

# Initialize and turn on debug logging
openstack.enable_logging(debug=False)

def main():
    # Initialize connection
    conn = openstack.connect()
    session = conn.session
    blazar = blazar_client(session=session)

    # list all hosts in blazar. For each one, we'll look them up in referenceAPI
    for blazar_host in blazar.host.list():
        print(blazar_host)




if __name__ == "__main__":
    main()
