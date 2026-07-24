# Observed Failure-Mode Matrix

**Version:** 1.0  
**Updated:** 2026-07-24  
**Status:** Directly observed system behavior supplied during existing-system verification

## Failure-mode diagram

```mermaid
flowchart TD
    Normal["Normal operation<br/>RFID entry and hardware exit available"]

    PiLoss["Pi power lost"]
    ReaderLoss["Reader disconnected"]
    GPIOLoss["GPIO/control conductor lost"]
    SupplyLoss["12 V access-control supply lost"]
    ACLoss["Facility AC lost"]
    UPSMode["UPS operation<br/>approximately 5–10 minutes"]
    Exhausted["UPS exhausted"]

    ExitOnly["Hardware exit available<br/>RFID entry unavailable"]
    ProcessNoActuation["Reader/software may operate<br/>but no physical release occurs"]
    ElectronicReleased["Maglock disengaged<br/>powered latch/strike released"]
    MechanicalSecure["Separate mechanical exterior lock<br/>continues preventing outside entry"]

    Normal --> PiLoss --> ExitOnly
    Normal --> ReaderLoss --> ExitOnly
    Normal --> GPIOLoss --> ProcessNoActuation
    ProcessNoActuation --> ExitOnly

    Normal --> SupplyLoss --> ElectronicReleased
    ElectronicReleased --> MechanicalSecure

    Normal --> ACLoss --> UPSMode
    UPSMode --> Normal: AC restored
    UPSMode --> Exhausted
    Exhausted --> ElectronicReleased
```

## Observed results

| Failure condition | Observed component behavior | Operational result |
|---|---|---|
| Pi power loss | Exit button works; maglock works; powered latch works; RFID does not work | People can exit; RFID entry is unavailable |
| Reader disconnected | Exit button works; maglock works; powered latch works; RFID does not work | People can exit; RFID entry is unavailable |
| GPIO/control-wire loss | Exit button works; maglock works; powered latch works; reader/software still work, but credentials have no effect on either lock | People can exit; RFID entry is functionally unavailable |
| 12 V supply loss | Powered latch releases and maglock disengages; reader may continue working but does nothing to the locks | Electronic locking is removed; separate mechanical lock prevents outside entry |
| Facility AC loss | UPS preserves normal operation for approximately 5–10 minutes | Door remains electronically operational during UPS reserve |
| UPS exhaustion / complete electronic power loss | Electronic locks de-energize | Separate mechanical exterior lock remains the outside-entry barrier |

## Conclusions

### Egress

The hardware exit path is independent of:

- Pi operation;
- RFID reader availability;
- GPIO control continuity.

This is a strong existing-system behavior that the replacement must preserve.

### RFID entry

RFID entry requires all of the following:

- functioning reader;
- functioning Pi and watcher;
- functioning authorization data;
- functioning GPIO23 path;
- functioning relay board;
- available 12 V lock power.

Failure of any upstream control component removes RFID entry without disabling the exit button.

### Electronic lock power

Both the maglock and powered latch/strike de-energize on loss of 12 V power. The system therefore cannot rely on either electronic device for exterior security after total lock-power loss.

### Mechanical fallback

A separate mechanical exterior lock remains independent of electronic power. It is the final outside-entry barrier after the UPS is exhausted or the 12 V system is unavailable.

## Questions closed by observation

- What happens when the Pi dies?
- What happens when the reader is disconnected?
- What happens when GPIO/control continuity is lost?
- What happens when the 12 V supply fails?
- What happens when facility AC fails?
- How long does the current UPS approximately preserve normal operation?
- What prevents outside entry after total electronic power loss?

## Questions still outside the verified scope

- Exact boot-time GPIO transient before the application establishes normal operation.
- Exact custom-board component-level topology.
- Exact internal exit-button circuit and the reason its connected harness is required.
- Whether the inaccessible live filesystem differs from the preserved backup.
- Exact historical cause of prior intermittent reader outages.
