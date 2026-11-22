# Enrolling Hardware into the Scavenger Compute Fleet

> **Runbook version:** 1.0  
> **Target Audience:** Operator enrolling hardware (Micro PCs, workstations, laptops, Mac minis) into a local scavenger compute pool.  

This guide provides step-by-step checklists to enroll any Linux or macOS machine into `~/.config/scavenger/fleet.json` so DecafClaw can borrow its CPU, RAM, or GPU over SSH without human intervention.

---

## 1. Prerequisites (On Controller)

Generate a dedicated SSH keypair for the controller if one does not already exist:
```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_scavenger -N "" -C "decafclaw-scavenger"
cat ~/.ssh/id_ed25519_scavenger.pub
```

Configure `~/.ssh/config` on the controller with an alias for each target machine:
```ssh-config
Host scavenger-desktop
    HostName desktop.local
    User scavenger
    IdentityFile ~/.ssh/id_ed25519_scavenger
    IdentitiesOnly yes

Host scavenger-laptop
    HostName laptop.local
    User scavenger
    IdentityFile ~/.ssh/id_ed25519_scavenger
    IdentitiesOnly yes
```

---

## 2. Enrollment Checklist by Operating System

### Flow A: Linux Workstation or Micro PC (Debian, Ubuntu, Fedora, Bazzite, Arch)
*Ideal for: OptiPlex micro PCs, ThinkCentres, NUCs, custom desktop rigs.*

#### 1. Hardware & BIOS (Wake-on-LAN)
- Enter BIOS/UEFI on boot.
- Enable **Wake on LAN** (or **Power on by PCIE/Onboard LAN**).
- Set **After Power Loss** to **Power On** or **Last State** (so headless micro PCs recover from power blips).
- In Linux, verify NIC has WoL enabled:
  ```bash
  sudo ethtool <interface_name> | grep "Wake-on"
  # If 'Wake-on: d', enable it:
  sudo ethtool -s <interface_name> wol g
  ```
- Note down the MAC address:
  ```bash
  ip link show <interface_name>
  ```

#### 2. Create Isolated `scavenger` User
```bash
# Create service user with bash shell
sudo useradd -m -s /bin/bash scavenger

# Lock down home directory (0700) so scavenger cannot share state
sudo chmod 700 /home/scavenger

# Enable systemd user lingering so rootless containers run without active login
sudo loginctl enable-linger scavenger

# Install the controller public key
sudo mkdir -p /home/scavenger/.ssh
sudo chmod 700 /home/scavenger/.ssh
echo "<controller-public-key>" | sudo tee /home/scavenger/.ssh/authorized_keys > /dev/null
sudo chmod 600 /home/scavenger/.ssh/authorized_keys
sudo chown -R scavenger:scavenger /home/scavenger/.ssh
```

#### 3. Rootless Container Engine (Podman or Docker)
For **Podman**:
```bash
# Verify subuids are allocated
grep scavenger /etc/subuid /etc/subgid
# If missing, allocate 65536 subuids:
sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 scavenger

# Verify rootless podman works as scavenger:
sudo -u scavenger podman run --rm alpine uname -a
```

#### 4. Blast Radius Containment
Ensure your personal operator account home directory is not readable by others:
```bash
chmod 700 /home/<your-user>
```

---

### Flow B: Apple Silicon Mac (macOS Darwin)
*Ideal for: MacBook Pro, MacBook Air, Mac mini, Mac Studio.*

#### 1. Create Dedicated macOS `scavenger` User
Open **Terminal** on macOS as an admin user:
```bash
# Find next available UID (e.g. 502)
id -u

# Create unprivileged service user
sudo dscl . -create /Users/scavenger
sudo dscl . -create /Users/scavenger UserShell /bin/zsh
sudo dscl . -create /Users/scavenger RealName "DecafClaw Scavenger"
sudo dscl . -create /Users/scavenger UniqueID 502
sudo dscl . -create /Users/scavenger PrimaryGroupID 20
sudo dscl . -create /Users/scavenger NFSHomeDirectory /Users/scavenger

# Create and lock down home directory
sudo mkdir -p /Users/scavenger
sudo chown scavenger:staff /Users/scavenger
sudo chmod 700 /Users/scavenger

# Add to SSH remote login access group
sudo dscl . -append /Groups/com.apple.access_ssh GroupMembership scavenger

# Install controller public key
sudo mkdir -p /Users/scavenger/.ssh
sudo chmod 700 /Users/scavenger/.ssh
echo "<controller-public-key>" | sudo tee /Users/scavenger/.ssh/authorized_keys > /dev/null
sudo chmod 600 /Users/scavenger/.ssh/authorized_keys
sudo chown -R scavenger:staff /Users/scavenger/.ssh
```

#### 2. Headless Docker Desktop Setup
Ensure non-interactive SSH sessions can access the Docker socket without the macOS keychain:
```bash
# Allow staff group write access to docker socket
sudo chmod g+w /var/run/docker.sock

# Configure empty auth config for scavenger user (bypasses macOS desktop credsStore trap)
sudo -u scavenger mkdir -p /Users/scavenger/.docker
echo '{"auths":{}}' | sudo -u scavenger tee /Users/scavenger/.docker/config.json > /dev/null

# Symlink docker binary into /usr/local/bin if not present
sudo ln -sf /Applications/Docker.app/Contents/Resources/bin/docker /usr/local/bin/docker
```

#### 3. Power Management & DarkWake
```bash
# Enable Wake on Magic Packet
sudo pmset -a womp 1

# Note Wi-Fi and Ethernet MAC addresses:
networksetup -getmacaddress en0
```

#### 4. Blast Radius Containment
```bash
chmod 700 /Users/<your-user>
```

---

## 3. Registering the Node in `~/.config/scavenger/fleet.json`

Add the newly provisioned node to your local `~/.config/scavenger/fleet.json` configuration file:

```json
{
  "name": "desktop-linux",
  "ssh_alias": "scavenger-desktop",
  "os": "linux",
  "arch": "x86_64",
  "priority": 10,
  "container": {
    "engine": "podman",
    "needs_selinux_z": false
  },
  "power": {
    "wol_mac": "AA:BB:CC:DD:EE:01",
    "wol_wait_seconds": 10
  }
}
```

Verify enrollment with:
```bash
python contrib/skills/scavenger/scavenger.py probe
```
