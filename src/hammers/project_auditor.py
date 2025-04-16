"""Module to clean up resources by expired projects"""

import argparse
import asyncio
import itertools
import json
import logging
from dataclasses import dataclass
from typing import Dict, Generator, Iterable

import openstack
import openstack.identity
import openstack.identity.v3
import openstack.identity.v3.domain
from aiohttp import ClientSession
from openstack.block_storage.v3._proxy import Proxy as CinderProxy
from openstack.compute.v2._proxy import Proxy as NovaProxy
from openstack.connection import Connection as OSConnection
from openstack.image.v2._proxy import Proxy as GlanceProxy
from openstack.load_balancer.v2._proxy import Proxy as OctaviaProxy
from openstack.network.v2._proxy import Proxy as NeutronProxy
from openstack.resource import Resource as OSResource

logging.basicConfig(level=logging.INFO)

openstack.enable_logging(debug=False)
LOG = logging.getLogger(__name__)


def _get_os_resources(conn: OSConnection) -> Generator[OSResource, None, None]:
    neutron_proxy: NeutronProxy = conn.network  # type: ignore
    compute_proxy: NovaProxy = conn.compute  # type:ignore
    image_proxy: GlanceProxy = conn.image  # type:ignore
    # block_storage_proxy: CinderProxy = conn.block_storage  # type:ignore
    # load_balancer_proxy: OctaviaProxy = conn.load_balancer  # type:ignore

    resources_generator = itertools.chain.from_iterable(
        [
            neutron_proxy.networks(),
            neutron_proxy.routers(),
            neutron_proxy.ips(),
            neutron_proxy.ports(),
            neutron_proxy.subnets(),
            compute_proxy.servers(details=False, all_projects=True),
            compute_proxy.server_groups(all_projects=True),
            image_proxy.images(),
            # block_storage_proxy.volumes(all_projects=True),
            # block_storage_proxy.snapshots(all_projects=True),
            # block_storage_proxy.backups(),
            # load_balancer_proxy.load_balancers(),
        ]
    )
    return resources_generator  # type: ignore


@dataclass
class ProjectInfo:
    charge_code: str
    nickname: str
    pi: str
    status: str
    expiration_date: str
    is_active: bool
    has_pending_allocation: bool


async def check_project_allocation(
    session: ClientSession, charge_code, api_token
) -> ProjectInfo:
    allocations_url = f"https://chameleoncloud.org/admin/allocations/api/view/{charge_code}/?token={api_token}"
    async with session.get(url=allocations_url) as resp:
        data = await resp.json()
        return ProjectInfo(**data)


async def lookup_project_info(
    charge_codes: Iterable[str], api_token: str
) -> Iterable[ProjectInfo]:
    async with ClientSession() as session:
        tasks = (
            check_project_allocation(
                session=session, charge_code=code, api_token=api_token
            )
            for code in charge_codes
            if code
        )
        results = await asyncio.gather(*tasks)
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--cloud",
        help="item in clouds.yaml to connect to, same as OS_CLOUD",
    )
    parser.add_argument(
        "--portal_token",
    )
    parser.add_argument(
        "--outputfile",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    conn = openstack.connect(cloud=args.cloud)

    cham_domain: openstack.identity.v3.domain.Domain
    cham_domain = conn.get_domain(name_or_id="chameleon")

    all_projects_list = conn.list_projects(domain_id=cham_domain.id)

    # project_id_to_name = {p.id: p.name for p in all_projects_list}
    project_name_to_id = {p.name: p.id for p in all_projects_list}

    # list of portal projects, has charge code but not keystone ID
    portal_projects_info = asyncio.run(
        lookup_project_info(
            charge_codes=project_name_to_id.keys(),
            api_token=args.portal_token,
        )
    )

    projects_by_id = {
        p.id: {
            "name": p.name,
            "is_enabled": p.is_enabled,
            "is_active": None,
        }
        for p in all_projects_list
    }

    # update project status in dict indexed by ID
    for proj_info in portal_projects_info:
        if not proj_info:
            continue
        project_id = project_name_to_id.get(proj_info.charge_code)
        projects_by_id[project_id]["is_active"] = proj_info.is_active

    expired_resources_by_chargecode = {}

    # for each resource, append to
    resources = _get_os_resources(conn=conn)
    for res in resources:
        project_id = None
        if hasattr(res, "project_id"):
            project_id = res.project_id
        elif hasattr(res, "owner_id"):
            project_id = res.owner_id
        else:
            LOG.warning("resource %s:%s has no project id", res.resources_key, res.id)

        project: Dict = projects_by_id.get(project_id, {})
        charge_code = project.get("name")
        is_active = project.get("is_active")
        if charge_code and not is_active:
            expired_resources_by_chargecode.setdefault(charge_code, [])
            expired_resources_by_chargecode[charge_code].append(
                {
                    "type": res.resources_key,
                    "id": res.id,
                }
            )

    with open(args.outputfile, "w") as file:
        json.dump(expired_resources_by_chargecode, file, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
