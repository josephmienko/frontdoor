# Existing Power Distribution and Lock-Side Schematic

**Version:** 2.0  
**Updated:** 2026-07-24  
**Status:** Functional as-built schematic based on measurements, continuity, wiring inspection, and observed failure behavior

## Power and control diagram

```mermaid
flowchart TD
    AC["120 V facility AC"]

    subgraph UPS["CyberPower SL700U UPS"]
        UPSCore["Battery-backed AC output"]
        Battery["Internal SLA battery<br/>observed reserve about 5–10 minutes"]
    end

    PiAdapter["120 V AC to 5 V DC<br/>USB Pi adapter"]
    Pi["Raspberry Pi 1 Model B+"]
    Reader["ID-20LA + SparkFun USB reader"]

    Supply12["120 V AC to 12 V DC<br/>access-control supply"]

    subgraph ControlDomain["5 V Pi / control domain"]
        GPIO["Physical pin 16 / BCM23<br/>0 V idle; ~3.3 V active"]
        Board5V["Custom-board 5 V and ground"]
        Driver["Discrete relay driver"]
        Coil["DS2E-S-DC5V relay coil"]
    end

    subgraph LockDomain["12 V electronic lock domain"]
        TB["Terminal block 21/22–29"]
        Exit["Illuminated EXIT button<br/>electrically integral harness"]
        Maglock["Electromagnetic lock<br/>de-energizes on 12 V loss"]
        PoweredLatch["Powered latch / strike<br/>releases on 12 V loss"]
        RelayContacts["Custom-board lock-side contacts"]
    end

    MechanicalLock["Separate mechanical exterior lock<br/>independent of AC, UPS, Pi, GPIO, and 12 V supply"]

    AC --> UPSCore
    Battery <--> UPSCore

    UPSCore --> PiAdapter --> Pi
    Pi -->|"USB power and serial"| Reader

    UPSCore --> Supply12 --> TB

    Pi -->|"5 V and GND"| Board5V
    Pi --> GPIO
    GPIO --> Driver
    Board5V --> Driver --> Coil
    Coil -.->|"mechanical isolation"| RelayContacts

    RelayContacts --> TB
    Exit --> TB
    TB --> Maglock
    TB --> PoweredLatch

    MechanicalLock -.->|"continues blocking outside entry<br/>after electronic power loss"| PoweredLatch

    note1["Pi, reader, or GPIO-path loss:<br/>hardware exit remains usable;<br/>RFID entry becomes unavailable."]
    ControlDomain --- note1

    note2["12 V loss:<br/>maglock disengages and powered latch releases;<br/>reader/software commands have no physical effect."]
    LockDomain --- note2

    note3["Facility AC loss:<br/>UPS initially preserves both domains;<br/>reserve is approximately 5–10 minutes."]
    UPS --- note3
```

## Confirmed distribution

- The UPS backs both the Pi power adapter and the 12 V access-control supply.
- The Pi powers the RFID reader over USB.
- The Pi supplies 5 V, ground, and GPIO23 to the custom board.
- The relay coil operates at approximately 5 V and is not driven directly by the 3.3 V GPIO.
- The relay mechanically separates the control and lock domains.
- The 12 V supply powers the electronic lock circuit and exit-button electronics.
- A separate mechanical exterior lock remains independent of all electronic power.

## Measured normal behavior

| Condition | GPIO23 | Relay side |
|---|---:|---:|
| Idle after completed access cycle | Approximately 0 V | Inactive |
| Authorized credential | Approximately 3.3 V for about three seconds | Approximately 5 V / active |
| End of release interval | Returns to approximately 0 V | Inactive |

## Observed power-failure behavior

| Power/control loss | Observed lock behavior | Entry/exit consequence |
|---|---|---|
| Pi power lost | Electronic locks and exit hardware remain otherwise operational | Exit available; RFID entry unavailable |
| Reader disconnected | No change to lock-side operation | Exit available; RFID entry unavailable |
| GPIO conductor lost | Lock-side system remains operational but cannot receive Pi release command | Exit available; credential may be read/logged but cannot release locks |
| 12 V supply lost | Maglock disengages; powered latch releases | Electronic access control is unavailable; separate mechanical lock prevents outside entry |
| Facility AC lost | UPS preserves operation for approximately 5–10 minutes | Normal operation continues during reserve; afterward electronic locks de-energize |
| UPS exhausted | Same electronic state as 12 V and Pi power loss | Separate mechanical lock remains the outside-entry barrier |

## Lock-side continuity findings

- Terminals 23, 24, 27, and 29 are on the measured +12 V node.
- Terminals 21/22 and 28 are on the measured return node.
- Terminals 25 and 26 measure approximately 0.1 ohm on the custom board.
- An approximately 54-ohm path from terminal 27 to terminals 25/26 appears through the connected exit-button assembly.
- Removing the exit-button harness prevents normal relay-board/lock operation.

These findings establish the functional role of the exit assembly but do not justify asserting a complete component-level schematic.

## Safety interpretation

The electronic maglock and powered latch behave as de-energize-to-release devices on 12 V loss. Exterior security during total electronic power loss is provided by the separate mechanical lock, not by the powered devices.
