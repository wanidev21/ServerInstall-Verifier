"""고객 정보가 없는 Linux/Redfish 시연 자료. 수집부터 보고서까지 검증할 때 사용한다."""
import json
from pathlib import Path


def write_fixture(folder):
    folder = Path(folder)
    sos, mock = folder/'sos', folder/'mock'
    def text(name, content):
        p = sos/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content, encoding='utf-8')
    def put(name, content):
        p = mock/name.lstrip('/')/'index.json'; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(content), encoding='utf-8')
    text('etc/os-release', 'PRETTY_NAME="Demo Linux"\n')
    text('etc/hostname', 'demo-node\n')
    text('proc/version', 'Linux version 4.18.0-demo\n')
    text('proc/cpuinfo', '\n\n'.join('processor : %d\nphysical id : 0\nmodel name : Demo CPU' % i for i in range(2)))
    text('proc/meminfo', 'MemTotal: 32505856 kB\n')
    text('sos_commands/hardware/dmidecode',
         'Handle 0x0001, DMI type 1\nSystem Information\n\tProduct Name: Demo Server\n\tSerial Number: DEMO001\n'
         '\nHandle 0x0002, DMI type 17\nMemory Device\n\tSize: 32 GB\n\tLocator: DIMM1\n'
         '\tSpeed: 3200 MT/s\n\tConfigured Memory Speed: 3200 MT/s\n')
    text('sos_commands/pci/lspci_-nnvv',
         '01:00.0 Ethernet controller [0200]: Demo NIC [15b3:1017]\n\tPhysical Slot: 1\n'
         '\tLnkCap: Speed 8GT/s, Width x8\n\tLnkSta: Speed 8GT/s, Width x8\n\tKernel driver in use: mlx5_core\n')
    text('sos_commands/kernel/dmesg', '[0.1] normal boot\n')
    text('sos_commands/systemd/timedatectl', 'Universal time: Thu 2026-10-29 00:00:00 UTC\n'
         'System clock synchronized: yes\nTime zone: UTC\n')
    text('sos_commands/systemd/systemctl_list-units_--failed', '0 loaded units listed.\n')
    text('sos_commands/networking/ip_-d_address', '1: eth0: <UP>\n    link/ether 02:00:00:00:00:01\n    inet 192.0.2.1/24\n')
    text('sos_commands/networking/ethtool_-i_eth0', 'driver: mlx5_core\nbus-info: 0000:01:00.0\n')
    text('sos_commands/networking/ethtool_eth0', 'Speed: 10000Mb/s\nLink detected: yes\n')
    text('sos_commands/block/ls_-lanR_.sys.block', 'lrwxrwxrwx 1 root root 0 sda -> ../devices/pci0000:00/demo\n')
    text('sos_commands/block/lsblk', 'sda 8:0 0 931.3G 0 disk\n')
    text('sos_commands/block/udevadm_info_.dev.sda', 'E: ID_MODEL=Demo_Disk\nE: ID_VENDOR=Demo\n')
    base = '/redfish/v1'
    status = {'State': 'Enabled', 'Health': 'OK'}
    def ref(p): return {'@odata.id': base+p}
    def col(p, *members): put(base+p, {'Members': [ref(m) for m in members]})
    put(base, {k: ref('/'+k) for k in ('Systems','Chassis','Managers','UpdateService')})
    col('/Systems','/Systems/1'); col('/Chassis','/Chassis/1'); col('/Managers','/Managers/1')
    system = {'Model':'Demo Server','SerialNumber':'DEMO001','PowerState':'On','Status':status,'BiosVersion':'1.0',
              'PCIeDevices':[ref('/PCIeDevices/1')]}
    system.update({k:ref('/Systems/1/'+k) for k in ('Processors','Memory','Storage')})
    put(base+'/Systems/1', system)
    col('/Systems/1/Processors','/Processors/1')
    put(base+'/Processors/1', {'Id':'1','Model':'Demo CPU','Socket':'0','TotalCores':1,'TotalThreads':2,'Status':status})
    col('/Systems/1/Memory','/Memory/1')
    put(base+'/Memory/1', {'Id':'1','DeviceLocator':'DIMM1','CapacityMiB':32768,'OperatingSpeedMhz':3200,'Status':status})
    col('/Systems/1/Storage','/Storage/1')
    put(base+'/Storage/1', {'Name':'Demo RAID','Drives':[ref('/Drives/1'),ref('/Drives/2')],'Volumes':ref('/Volumes')})
    for i in (1,2):
        put(base+'/Drives/%d'%i, {'Name':'Drive%d'%i,'Model':'Demo Disk','CapacityBytes':1000000000000,'MediaType':'SSD','Status':status})
    col('/Volumes','/Volumes/1')
    put(base+'/Volumes/1', {'Name':'RAID Volume1','RAIDType':'RAID1','CapacityBytes':1000000000000,
                          'Links':{'Drives':[ref('/Drives/1'),ref('/Drives/2')]},'Status':status})
    put(base+'/PCIeDevices/1', {'@odata.id':base+'/PCIeDevices/1','Name':'Demo NIC','Status':status,
        'Location':{'PartLocation':{'LocationOrdinalValue':1}}, 'PCIeFunctions':ref('/PCIeFunctions')})
    col('/PCIeFunctions','/PCIeFunctions/1')
    put(base+'/PCIeFunctions/1', {'VendorId':'0x15b3','DeviceId':'0x1017'})
    put(base+'/Chassis/1', {k:ref('/Chassis/1/'+k) for k in ('NetworkAdapters','Power','Thermal')})
    col('/Chassis/1/NetworkAdapters','/Adapters/1')
    put(base+'/Adapters/1', {'Name':'Demo NIC','NetworkPorts':ref('/Ports')})
    col('/Ports','/Ports/1')
    put(base+'/Ports/1', {'Id':'1','AssociatedNetworkAddresses':['02:00:00:00:00:01'],'LinkStatus':'Up'})
    put(base+'/Chassis/1/Power', {'PowerSupplies':[{'Name':'PSU%d'%i,'Model':'Demo PSU','PowerCapacityWatts':750,'Status':status} for i in (1,2)]})
    put(base+'/Chassis/1/Thermal', {'Fans':[{'Name':'Fan1','Reading':5000,'Status':status}],
                                  'Temperatures':[{'Name':'CPU1','ReadingCelsius':40,'UpperThresholdCritical':85,'Status':status}]})
    put(base+'/Managers/1', {'DateTime':'2026-10-29T00:00:00Z','FirmwareVersion':'1.0'})
    put(base+'/UpdateService', {'FirmwareInventory':ref('/FirmwareInventory')})
    col('/FirmwareInventory')
    return sos, mock
