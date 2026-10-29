import ast
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]

from serverinstall import checks, inventory, oscollect, redfish, report, source
from demo_fixture import write_fixture

def sample():
    now = datetime(2026, 10, 29, tzinfo=timezone.utc)
    dimm = {"slot": "DIMM1", "size_gib": 32, "speed": 3200, "configured_speed": 3200,
            "manufacturer": "Demo", "part": "DEMO-RDIMM", "health": "OK", "state": "Enabled"}
    pci = {"addr": "0000:01:00.0", "dev": "0000:01:00", "slot": "1", "name": "Demo NIC", "cls": "Ethernet",
           "cls_code": "0200", "vid": "15b3", "did": "1017", "driver": "mlx5_core",
           "cap_speed": 8, "sta_speed": 8, "cap_width": 8, "sta_width": 8}
    osd = {"source": "synthetic", "host": "demo-node", "product": "Demo Server", "serial": "DEMO001", "uuid": "",
           "os_name": "Demo Linux", "kernel": "demo", "cpus": [{"socket": "0", "model": "Demo CPU"}],
           "threads": 2, "dimms": [copy.deepcopy(dimm)], "mem_total_gib": 31,
           "pci": [pci], "nics": [{"name": "eth0", "mac": "02:00:00:00:00:01", "driver": "mlx5_core",
                                      "bus": "0000:01:00.0", "link": True, "speed": 10000, "ipv4": ["192.0.2.1"]}],
           "disks": [{"name": "sda", "size_bytes": 1000000000000, "transport": "local", "model": "Demo Disk", "vendor": "Demo"}],
           "kernel_log": ["[0.1] normal boot"], "failed_units": [], "missing": [],
           "time": {"utc": now, "tz": "UTC", "ntp_sync": True}}
    bmc = {"model": "Demo Server", "serial": "DEMO001", "health": "OK", "power": "On", "bios": "1.0",
           "bmc_fw": "1.0", "bmc_time": now, "errors": [], "firmware": [], "dimms": [copy.deepcopy(dimm)],
           "cpus": [{"socket": "0", "model": "Demo CPU", "cores": 1, "threads": 2, "state": "Enabled", "health": "OK"}],
           "pcie": [{"name": "Demo NIC", "slot": "1", "ids": {("15b3", "1017")}, "health": "OK"}],
           "ports": [{"adapter": "Demo NIC", "port": "1", "mac": "02:00:00:00:00:01", "link": "Up"}],
           "drives": [{"id": "drive1", "name": "Disk 1", "model": "Demo Disk", "size_bytes": 1000000000000,
                       "media": "SSD", "state": "Enabled", "health": "OK", "controller": "RAID"}],
           "volumes": [{"name": "Volume1", "controller": "RAID", "raid": "RAID1", "size_bytes": 1000000000000,
                        "drives": ["drive1"], "state": "Enabled", "health": "OK"}],
           "psus": [{"name": "PSU%d" % i, "model": "Demo PSU", "watts": 750,
                     "state": "Enabled", "health": "OK"} for i in (1, 2)],
           "power_redundancy": [], "fans": [{"name":"Fan1","state":"Enabled","health":"OK"}],
           "temps": [{"name":"CPU1","reading":40,"upper":85,"health":"OK"}]}
    bmc['drives'].append(dict(bmc['drives'][0], id='drive2', name='Disk 2'))
    bmc['volumes'][0]['drives'].append('drive2')
    return osd, bmc


class ChecksTests(unittest.TestCase):
    def setUp(self): self.osd, self.bmc = sample()
    def rows(self, area): return [r for r in checks.run(self.osd, self.bmc) if r["area"] == area]
    def test_healthy_cross_checks_pass(self):
        self.assertEqual(report.result(checks.run(self.osd, self.bmc)), "PASS")
    def test_missing_serial_is_not_match(self):
        self.osd["serial"] = ""
        self.assertIn("skip", [r["status"] for r in self.rows("시스템")])
        self.assertNotIn("ok", [r["status"] for r in self.rows("시스템")])
    def test_placeholder_serial_is_not_match(self):
        self.osd["serial"] = self.bmc["serial"] = "To be filled by O.E.M."
        self.assertEqual(self.rows("시스템")[0]["status"], "skip")
    def test_different_serial_fails(self):
        self.osd["serial"] = "DIFFERENT"
        self.assertEqual(self.rows("시스템")[0]["status"], "fail")
    def test_empty_cpu_is_not_failure_or_pass(self):
        self.bmc["cpus"] = []
        self.assertEqual(self.rows("CPU")[0]["status"], "skip")
    def test_missing_bmc_memory_is_not_hardware_fault(self):
        self.bmc["dimms"] = []
        self.assertNotIn("fail", [r["status"] for r in self.rows("메모리")])
        self.assertIn("skip", [r["status"] for r in self.rows("메모리")])
    def test_same_total_wrong_slots_fails(self):
        for owner in (self.osd, self.bmc):
            owner["dimms"].append(dict(owner["dimms"][0], slot="DIMM2", size_gib=64))
        self.osd["dimms"][0]["size_gib"], self.osd["dimms"][1]["size_gib"] = 64, 32
        self.assertTrue(any(r["status"] == "fail" and "슬롯별" in r["title"] for r in self.rows("메모리")))
    def test_unmatched_slot_names_are_incomplete(self):
        self.osd["dimms"][0]["slot"] = "CPU1_DIMM_A1"
        self.assertIn("skip", [r["status"] for r in self.rows("메모리")])
    def test_report_does_not_merge_cpu1_and_cpu2_slots(self):
        for owner in (self.osd, self.bmc):
            owner["dimms"][0]["slot"] = "CPU1_DIMM1"
            owner["dimms"].append(dict(owner["dimms"][0], slot="CPU2_DIMM1"))
        html = report.render_html(checks.run(self.osd, self.bmc), self.osd, self.bmc)
        self.assertIn("CPU1_DIMM1", html); self.assertIn("CPU2_DIMM1", html)
    def test_empty_drives_not_zero_healthy(self):
        self.bmc["drives"] = []; self.bmc["volumes"] = []
        self.assertIn("skip", [r["status"] for r in self.rows("디스크")])
        self.assertFalse(any("0개 정상" in r["title"] for r in self.rows("디스크")))
    def test_missing_collection_prevents_pass(self):
        self.bmc["errors"] = ["/redfish/v1/Systems/1/Memory: HTTP 500"]
        self.assertEqual(report.result(checks.run(self.osd, self.bmc)), "INCOMPLETE")
    def test_missing_cooling_prevents_pass(self):
        self.bmc['fans'] = []; self.bmc['temps'] = []
        self.assertEqual(report.result(checks.run(self.osd, self.bmc)), 'INCOMPLETE')
    def test_missing_bmc_pcie_prevents_pass(self):
        self.bmc['pcie'] = []
        self.assertEqual(report.result(checks.run(self.osd, self.bmc)), 'INCOMPLETE')
    def test_missing_bmc_ports_prevents_pass(self):
        self.bmc['ports'] = []
        self.assertEqual(report.result(checks.run(self.osd, self.bmc)), 'INCOMPLETE')
    def test_missing_bmc_mac_is_not_verified(self):
        self.bmc['ports'][0]['mac'] = ''
        self.assertEqual(report.result(checks.run(self.osd, self.bmc)), 'INCOMPLETE')
    def test_no_bmc_is_incomplete(self):
        rows = checks.run(self.osd, None)
        self.assertEqual(report.result(rows), "INCOMPLETE")
        self.assertIn("INCOMPLETE", report.console(rows, self.osd, None, "demo.html"))
    def test_failed_and_skipped_still_fail(self):
        self.osd["serial"] = "OTHER"; self.bmc["errors"] = ["missing"]
        self.assertEqual(report.result(checks.run(self.osd, self.bmc)), "FAIL")
    def test_warning_result(self):
        self.osd["time"]["ntp_sync"] = False
        self.assertEqual(report.result(checks.run(self.osd, self.bmc)), "WARN")
    def test_unknown_raid_disk_warns(self):
        self.osd["disks"] = []
        self.assertIn("warn", [r["status"] for r in self.rows("RAID")])
    def test_same_size_disks_are_candidates_not_match(self):
        self.osd["disks"].append(dict(self.osd["disks"][0], name="sdb"))
        v = inventory.raid_volumes(self.osd, self.bmc)[0]
        self.assertEqual(v["os_disk"], ""); self.assertEqual(v["os_candidates"], ["sda", "sdb"])
    def test_disabled_raid_fails(self):
        self.bmc["volumes"][0]["state"] = "Disabled"
        self.assertIn("fail", [r["status"] for r in self.rows("RAID")])
    def test_raid1_with_one_member_warns(self):
        self.bmc['volumes'][0]['drives'] = ['drive1']
        self.assertIn('warn', [r['status'] for r in self.rows('RAID')])
    def test_disabled_psu_fails(self):
        self.bmc["psus"][0]["state"] = "Disabled"
        self.assertIn("fail", [r["status"] for r in self.rows("전원")])
    def test_duplicate_pcie_ids_without_slots_are_not_guessed(self):
        self.osd["pci"].append(dict(self.osd["pci"][0], addr="0000:02:00.0", dev="0000:02:00", slot="2"))
        self.bmc["pcie"][0]["slot"] = ""
        self.assertIn("skip", [r["status"] for r in self.rows("PCIe")])
        report.render_html(checks.run(self.osd, self.bmc), self.osd, self.bmc)


class RedfishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.client = redfish.Client(mock_dir=self.tmp.name)
    def put(self, path, value):
        p = Path(self.tmp.name)/path.lstrip('/')/'index.json'
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(value), encoding="utf-8")
    def test_annotated_reference_is_fetched(self):
        self.put('/redfish/v1/Memory', {"Members": [{"@odata.id": "/redfish/v1/Memory/1", "@odata.type": "#Memory"}]})
        self.put('/redfish/v1/Memory/1', {"Id": "1", "CapacityMiB": 32768})
        self.assertEqual(self.client.members('/redfish/v1/Memory')[0]["CapacityMiB"], 32768)
    def test_array_and_collection_links(self):
        self.put('/redfish/v1/PCIe/1', {"Id": "1"})
        self.put('/redfish/v1/PCIe', {"Members": [{"@odata.id": "/redfish/v1/PCIe/1"}]})
        array = self.client.linked({"PCIeDevices": [{"@odata.id": "/redfish/v1/PCIe/1"}]}, "PCIeDevices")
        linked = self.client.linked({"PCIeDevices": {"@odata.id": "/redfish/v1/PCIe"}}, "PCIeDevices")
        self.assertEqual(array, linked)
    def test_paginated_collection(self):
        pages = {'/redfish/v1/Memory': {"Members": [{"Id": "1"}], "Members@odata.nextLink": '/redfish/v1/Memory?$skip=1'},
                 '/redfish/v1/Memory?$skip=1': {"Members": [{"Id": "2"}]}}
        with patch.object(self.client, 'get', side_effect=lambda p: pages[p]):
            self.assertEqual([x["Id"] for x in self.client.members('/redfish/v1/Memory')], ['1', '2'])
    def test_pagination_loop_stops_and_reports(self):
        with patch.object(self.client, 'get', return_value={"Members": [], "Members@odata.nextLink": '/redfish/v1/Memory'}):
            self.client.members('/redfish/v1/Memory')
        self.assertTrue(self.client.errors)
    def test_missing_optional_path_does_not_crash(self):
        self.assertIsNone(self.client.get(None)); self.assertTrue(self.client.errors)
    def test_external_ref_and_path_escape_not_read(self):
        self.assertIsNone(self.client.get('https://example.com/redfish/v1'))
        self.assertIsNone(self.client.get('/redfish/v1/../../private'))
    def test_unreachable_required_root_is_error(self):
        with self.assertRaises(redfish.RedfishError): self.client.get('/redfish/v1', required=True)


class SourceAndCLITests(unittest.TestCase):
    def test_full_mock_collection_to_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            sos, mock = write_fixture(tmp)
            osd = oscollect.collect(source.SosSource(str(sos)))
            bmc = redfish.collect(redfish.Client(mock_dir=str(mock)))
            self.assertEqual(bmc['errors'], [])
            self.assertEqual(len(bmc['pcie']), 1)
            rows = checks.run(osd, bmc)
            self.assertEqual(report.result(rows), 'PASS', rows)
            out = Path(tmp)/'reports'
            run = subprocess.run([sys.executable, str(ROOT/'serverinstall.py'), '--sos', str(sos),
                                  '--redfish-mock', str(mock), '-o', str(out)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
            self.assertEqual(run.returncode, 0, run.stderr+run.stdout)
            self.assertIn('Result : PASS', run.stdout)
            htmls = list(out.glob('*.html')); self.assertEqual(len(htmls), 1)
            self.assertIn('DEMO001', htmls[0].read_text(encoding='utf-8'))
    def test_full_mock_missing_memory_is_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            sos, mock = write_fixture(tmp)
            (mock/'redfish/v1/Memory/1/index.json').unlink()
            osd = oscollect.collect(source.SosSource(str(sos)))
            bmc = redfish.collect(redfish.Client(mock_dir=str(mock)))
            rows = checks.run(osd, bmc)
            self.assertEqual(report.result(rows), 'INCOMPLETE', rows)
            self.assertFalse(any(r['area']=='메모리' and r['status']=='fail' for r in rows))
    def test_partial_sos_produces_incomplete_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'output'
            run = subprocess.run([sys.executable, str(ROOT/'serverinstall.py'), '--sos', tmp, '--no-bmc', '-o', str(out)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertIn('INCOMPLETE', run.stdout)
            htmls = list(out.glob('*.html')); self.assertEqual(len(htmls), 1)
            self.assertIn('INCOMPLETE', htmls[0].read_text(encoding='utf-8'))
    def test_invalid_sos_is_clean_error(self):
        with self.assertRaises(ValueError): source.SosSource('does-not-exist-demo')
    def test_nonzero_command_with_stdout_is_unavailable(self):
        p = Mock(returncode=1); p.communicate.return_value = (b'permission denied', b'error')
        with patch('serverinstall.source.subprocess.Popen', return_value=p):
            src = source.LiveSource(); self.assertIsNone(src.cmd('lspci')); self.assertTrue(src.missing)
    def test_timed_out_child_is_reaped(self):
        p = Mock(); p.communicate.side_effect = [subprocess.TimeoutExpired('lspci', 60), (b'', b'')]
        with patch('serverinstall.source.subprocess.Popen', return_value=p):
            self.assertIsNone(source.LiveSource().cmd('lspci'))
        p.kill.assert_called_once(); self.assertEqual(p.communicate.call_count, 2)
    def test_supported_python36_syntax(self):
        paths = [ROOT/'serverinstall.py'] + list((ROOT/'serverinstall').glob('*.py'))
        paths += list((ROOT/'servercommon').glob('*.py'))
        for p in paths:
            with self.subTest(file=p.name): ast.parse(p.read_text(encoding='utf-8'), feature_version=(3, 6))
