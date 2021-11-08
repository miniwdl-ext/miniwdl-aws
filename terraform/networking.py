from constructs import Construct
from imports.aws.vpc import (
    Route,
    Vpc,
    Subnet,
    InternetGateway,
    RouteTable,
    RouteTableAssociation,
    SecurityGroup,
    SecurityGroupIngress,
    SecurityGroupEgress,
)


class MiniwdlAwsNetworking(Construct):
    vpc: Vpc
    subnet: Subnet
    sg: SecurityGroup

    def __init__(self, scope: Construct, ns: str, availability_zone: str):
        super().__init__(scope, ns)

        self.vpc = Vpc(
            self,
            "vpc",
            cidr_block="10.0.0.0/16",
            enable_dns_support=True,
            enable_dns_hostnames=True,
        )
        self.subnet = Subnet(
            self,
            "subnet",
            vpc_id=self.vpc.id,
            cidr_block="10.0.0.0/16",
            map_public_ip_on_launch=True,
            availability_zone=availability_zone,
        )
        igw = InternetGateway(self, "igw", vpc_id=self.vpc.id)
        route_table = RouteTable(self, "route-table", vpc_id=self.vpc.id)
        Route(
            route_table,
            "route",
            route_table_id=route_table.id,
            destination_cidr_block="0.0.0.0/0",
            gateway_id=igw.id,
        )
        RouteTableAssociation(
            self, "route-table-association", route_table_id=route_table.id, subnet_id=self.subnet.id
        )
        self.sg = SecurityGroup(
            self,
            "security-group",
            vpc_id=self.vpc.id,
            ingress=[
                SecurityGroupIngress(
                    from_port=0, to_port=0, protocol="-1", cidr_blocks=[self.vpc.cidr_block]
                )
            ],
            egress=[
                SecurityGroupEgress(
                    from_port=0, to_port=0, protocol="-1", cidr_blocks=["0.0.0.0/0"]
                )
            ],
        )
