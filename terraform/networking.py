from typing import List, Dict
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
    subnets_by_zone: Dict[str, Subnet]
    sg: SecurityGroup

    def __init__(self, scope: Construct, ns: str, availability_zones: List[str]):
        super().__init__(scope, ns)

        self.vpc = Vpc(
            self,
            "vpc",
            cidr_block="10.0.0.0/16",
            enable_dns_support=True,
            enable_dns_hostnames=True,
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

        self.subnets_by_zone = {}
        for i, zone in enumerate(availability_zones):
            subnet = Subnet(
                self,
                "subnet-" + zone,
                vpc_id=self.vpc.id,
                cidr_block=f"10.0.{i*32}.0/20",
                map_public_ip_on_launch=True,
                availability_zone=zone,
            )
            RouteTableAssociation(
                self,
                "route-table-association-" + zone,
                route_table_id=route_table.id,
                subnet_id=subnet.id,
            )
            self.subnets_by_zone[zone] = subnet

        self.sg = SecurityGroup(
            self,
            "security-group",
            name_prefix="miniwdl-",
            vpc_id=self.vpc.id,
            ingress=[
                SecurityGroupIngress(
                    from_port=0,
                    to_port=0,
                    protocol="-1",
                    cidr_blocks=[self.vpc.cidr_block],
                )
            ],
            egress=[
                SecurityGroupEgress(
                    from_port=0, to_port=0, protocol="-1", cidr_blocks=["0.0.0.0/0"]
                )
            ],
        )
