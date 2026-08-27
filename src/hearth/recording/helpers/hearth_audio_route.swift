// hearth_audio_route.swift — M7 P3 helper: mirror the CURRENT output device into
// BlackHole for the duration of a recording, then restore. CoreAudio, no deps.
//
// The problem it solves: BlackHole only hears audio ROUTED to it. Instead of the
// manual Audio-MIDI-Setup ritual (build a Multi-Output Device by hand, switch to
// it, switch back), this tool does the whole dance programmatically around a
// recording, adapting to whatever the user is listening on right now (built-in
// speakers, a Bluetooth speaker, HDMI — the mirror always wraps the live one).
//
// Commands (JSON on stdout, exit 0 = ok):
//   status              → current default output + whether BlackHole / a leftover
//                         mirror exists
//   engage              → create stacked aggregate "Hearth Record Mirror"
//                         [current default + BlackHole], drift-corrected, set it
//                         as default output; prints previous_uid for release
//   release <prev_uid>  → restore <prev_uid> as default output, destroy the mirror
//   repair              → heal a LEAKED mirror (crashed stop / killed bot): read
//                         the mirror's own sub-device list, restore the physical
//                         (non-BlackHole) sub as default, destroy the mirror.
//                         The 2026-07-28 16:35 incident: a bot killed mid-stop
//                         left the mirror as default; the next bot bound its
//                         output to it → the companion's voice fed BlackHole → every later
//                         take's music stem carried a delayed copy of the companion's voice
//                         (the "echo layers"). Run at every bot startup.
//
// Topology note (why the companion's voice is NOT doubled into the music stem): the bot's
// PortAudio output stream binds to the concrete device at startup — it does not
// follow later default-device changes. Apps that follow the system default
// (browsers, Music.app) reroute into the mirror; the bot keeps talking straight
// to the physical device. So the BlackHole stem carries the background audio
// only. (Caveat, documented in M7: launching the BOT while a mirror is engaged
// would bind it into the mirror → voice would double. recording.py releases the
// mirror on stop/shutdown, so this state doesn't persist.)
//
// Build (recording.py does this lazily):
//   swiftc -O tools/hearth_audio_route.swift -o tools/hearth-audio-route

import CoreAudio
import Foundation

let MIRROR_UID = "hearth-record-mirror"
let MIRROR_NAME = "Hearth Record Mirror"
let BLACKHOLE_UID = "BlackHole2ch_UID"

// ── tiny CoreAudio helpers ────────────────────────────────────────────────────

func sysProp<T>(_ sel: AudioObjectPropertySelector, as type: T.Type) -> [T] {
    var addr = AudioObjectPropertyAddress(
        mSelector: sel, mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject),
                                         &addr, 0, nil, &size) == noErr, size > 0
    else { return [] }
    let count = Int(size) / MemoryLayout<T>.size
    var buf = [T](unsafeUninitializedCapacity: count) { _, n in n = count }
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject),
                                     &addr, 0, nil, &size, &buf) == noErr
    else { return [] }
    return buf
}

func devString(_ dev: AudioObjectID, _ sel: AudioObjectPropertySelector) -> String? {
    var addr = AudioObjectPropertyAddress(
        mSelector: sel, mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<CFString?>.size)
    var value: Unmanaged<CFString>?
    guard AudioObjectGetPropertyData(dev, &addr, 0, nil, &size, &value) == noErr,
          let v = value else { return nil }
    return v.takeRetainedValue() as String
}

func findDevice(uid: String) -> AudioObjectID? {
    for d in sysProp(kAudioHardwarePropertyDevices, as: AudioObjectID.self)
    where devString(d, kAudioDevicePropertyDeviceUID) == uid { return d }
    return nil
}

func defaultOutput() -> AudioObjectID? {
    sysProp(kAudioHardwarePropertyDefaultOutputDevice, as: AudioObjectID.self).first
}

func subDeviceUIDs(_ dev: AudioObjectID) -> [String] {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioAggregateDevicePropertyFullSubDeviceList,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<CFArray?>.size)
    var list: Unmanaged<CFArray>?
    guard AudioObjectGetPropertyDataSize(dev, &addr, 0, nil, &size) == noErr,
          AudioObjectGetPropertyData(dev, &addr, 0, nil, &size, &list) == noErr,
          let arr = list?.takeRetainedValue() as? [String] else { return [] }
    return arr
}

func destroyWithPoll(_ dev: AudioObjectID) -> Bool {
    // Destroy is ASYNC in coreaudiod and DROPPED if the process exits first
    // (verified live 2026-07-28) — poll until the device is really gone.
    guard AudioHardwareDestroyAggregateDevice(dev) == noErr else { return false }
    for _ in 0..<25 {  // up to ~2.5 s
        usleep(100_000)
        if findDevice(uid: MIRROR_UID) == nil { return true }
    }
    return false
}

func setDefaultOutput(_ dev: AudioObjectID) -> Bool {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultOutputDevice,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var id = dev
    return AudioObjectSetPropertyData(
        AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil,
        UInt32(MemoryLayout<AudioObjectID>.size), &id) == noErr
}

func nominalRate(_ dev: AudioObjectID) -> Float64? {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyNominalSampleRate,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<Float64>.size)
    var rate: Float64 = 0
    guard AudioObjectGetPropertyData(dev, &addr, 0, nil, &size, &rate) == noErr,
          rate > 0 else { return nil }
    return rate
}

func setNominalRate(_ dev: AudioObjectID, _ rate: Float64) -> Bool {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyNominalSampleRate,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var r = rate
    guard AudioObjectSetPropertyData(dev, &addr, 0, nil,
                                     UInt32(MemoryLayout<Float64>.size), &r) == noErr
    else { return false }
    for _ in 0..<15 {  // rate changes are async in coreaudiod — poll ≤1.5 s
        if let now = nominalRate(dev), abs(now - rate) < 1 { return true }
        usleep(100_000)
    }
    return false
}

func fail(_ msg: String) -> Never {
    print("{\"ok\": false, \"error\": \"\(msg)\"}")
    exit(1)
}

func jstr(_ s: String?) -> String {
    guard let s = s else { return "null" }
    let esc = s.replacingOccurrences(of: "\\", with: "\\\\")
               .replacingOccurrences(of: "\"", with: "\\\"")
    return "\"\(esc)\""
}

// ── commands ──────────────────────────────────────────────────────────────────

let args = CommandLine.arguments
let cmd = args.count > 1 ? args[1] : "status"

switch cmd {
case "status":
    let out = defaultOutput()
    let outUID = out.flatMap { devString($0, kAudioDevicePropertyDeviceUID) }
    let outName = out.flatMap { devString($0, kAudioObjectPropertyName) }
    let mirror = findDevice(uid: MIRROR_UID)
    let subs = mirror.map { subDeviceUIDs($0).map { jstr($0) }.joined(separator: ", ") } ?? ""
    // Diagnostic depth (2026-08-23): when the mirror is live, read back which
    // sub-device coreaudiod ACTUALLY made clock master, plus every rate in play —
    // the capture-shortfall hunt needs ground truth, not the desc we requested.
    var extra = ""
    if let m = mirror {
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioAggregateDevicePropertyMainSubDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        var size = UInt32(MemoryLayout<CFString?>.size)
        var master: Unmanaged<CFString>?
        var masterUID: String? = nil
        if AudioObjectGetPropertyData(m, &addr, 0, nil, &size, &master) == noErr,
           let mv = master { masterUID = mv.takeRetainedValue() as String }
        let mirrorRate = nominalRate(m).map { String(Int($0)) } ?? "null"
        let bhRate = findDevice(uid: BLACKHOLE_UID).flatMap { nominalRate($0) }
            .map { String(Int($0)) } ?? "null"
        extra = ", \"mirror_master\": \(jstr(masterUID)), " +
                "\"mirror_rate\": \(mirrorRate), \"blackhole_rate\": \(bhRate)"
    }
    print("{\"ok\": true, \"default_output\": \(jstr(outName)), " +
          "\"default_output_uid\": \(jstr(outUID)), " +
          "\"blackhole\": \(findDevice(uid: BLACKHOLE_UID) != nil), " +
          "\"mirror_exists\": \(mirror != nil), \"mirror_subs\": [\(subs)]\(extra)}")

case "repair":
    guard let mirror = findDevice(uid: MIRROR_UID) else {
        print("{\"ok\": true, \"repaired\": false, \"reason\": \"no mirror\"}")
        exit(0)
    }
    // The mirror itself remembers which physical device it wrapped.
    let physUID = subDeviceUIDs(mirror).first { $0 != BLACKHOLE_UID }
    var restoredName: String? = nil
    let defUID = defaultOutput().flatMap { devString($0, kAudioDevicePropertyDeviceUID) }
    if defUID == MIRROR_UID || defUID == BLACKHOLE_UID {
        // Restore the wrapped physical device; fall back to any real output.
        var target = physUID.flatMap { findDevice(uid: $0) }
        if target == nil {
            for d in sysProp(kAudioHardwarePropertyDevices, as: AudioObjectID.self)
            where d != mirror && devString(d, kAudioDevicePropertyDeviceUID) != BLACKHOLE_UID {
                var addr = AudioObjectPropertyAddress(
                    mSelector: kAudioDevicePropertyStreams,
                    mScope: kAudioDevicePropertyScopeOutput,
                    mElement: kAudioObjectPropertyElementMain)
                var size: UInt32 = 0
                if AudioObjectGetPropertyDataSize(d, &addr, 0, nil, &size) == noErr, size > 0 {
                    target = d
                    break
                }
            }
        }
        if let t = target, setDefaultOutput(t) {
            restoredName = devString(t, kAudioObjectPropertyName)
        }
    }
    let destroyed = destroyWithPoll(mirror)
    print("{\"ok\": \(destroyed), \"repaired\": true, " +
          "\"restored\": \(jstr(restoredName)), \"destroyed\": \(destroyed)}")

case "engage":
    guard let bh = findDevice(uid: BLACKHOLE_UID) else { fail("BlackHole device not found") }
    guard let cur = defaultOutput(),
          let curUID = devString(cur, kAudioDevicePropertyDeviceUID)
    else { fail("no default output device") }
    let curName = devString(cur, kAudioObjectPropertyName) ?? "?"
    if curUID == BLACKHOLE_UID { fail("default output IS BlackHole — refusing (you would lose audio)") }
    if curUID == MIRROR_UID { fail("mirror already engaged") }
    // Leftover mirror from a crashed run → destroy before rebuilding around the
    // CURRENT device (it may have changed since).
    if let stale = findDevice(uid: MIRROR_UID) {
        _ = destroyWithPoll(stale)
    }
    // Rate-agnostic capture: the aggregate clocks at the MASTER (physical)
    // device, so a BlackHole nominal that differs from it lands the drift-
    // compensation skew in the CAPTURE leg (44.1 k BT headset vs 48 k BlackHole
    // = fast/wobbly music stem). Align BlackHole TO the master before building
    // the aggregate — never the reverse: touching the physical device's rate
    // could glitch live audio — and report the capture rate so the recorder
    // labels the stem truthfully whatever device is in use.
    let physRate = nominalRate(cur)
    var capRate = nominalRate(bh) ?? 48000
    var aligned = true
    if let pr = physRate {
        if abs(capRate - pr) >= 1 {
            aligned = setNominalRate(bh, pr)
            capRate = nominalRate(bh) ?? capRate
        }
    } else { aligned = false }
    // Stacked (multi-output) aggregate: BLACKHOLE is the clock master, the
    // physical device drift-compensated against it. The record leg must own the
    // clock: BlackHole is virtual and host-clocked (rock solid), while physical
    // devices — Bluetooth sinks especially — stall and re-buffer, and a stall in
    // the MASTER freezes the whole aggregate, silently starving the loopback leg
    // (~6% shortfall + stall-edge clicks, measured 2026-08-23 on a BT headset).
    // With BlackHole as master, BT jitter lands transiently in the MONITORING
    // ear, never in the capture.
    let desc: [String: Any] = [
        kAudioAggregateDeviceUIDKey as String: MIRROR_UID,
        kAudioAggregateDeviceNameKey as String: MIRROR_NAME,
        kAudioAggregateDeviceIsStackedKey as String: 1,
        kAudioAggregateDeviceMainSubDeviceKey as String: BLACKHOLE_UID,
        kAudioAggregateDeviceSubDeviceListKey as String: [
            [kAudioSubDeviceUIDKey as String: BLACKHOLE_UID,
             kAudioSubDeviceDriftCompensationKey as String: 0],
            [kAudioSubDeviceUIDKey as String: curUID,
             kAudioSubDeviceDriftCompensationKey as String: 1],
        ],
    ]
    var aggID = AudioObjectID(0)
    guard AudioHardwareCreateAggregateDevice(desc as CFDictionary, &aggID) == noErr
    else { fail("aggregate creation failed") }
    // The HAL needs a beat before the new device accepts default-output.
    usleep(300_000)
    guard setDefaultOutput(aggID) else {
        _ = AudioHardwareDestroyAggregateDevice(aggID)
        fail("could not set mirror as default output (mirror destroyed, audio untouched)")
    }
    print("{\"ok\": true, \"previous_uid\": \(jstr(curUID)), " +
          "\"previous_name\": \(jstr(curName)), \"mirror_id\": \(aggID), " +
          "\"rate\": \(Int(capRate)), \"rate_aligned\": \(aligned)}")

case "release":
    guard args.count > 2 else { fail("usage: release <previous_uid>") }
    let prevUID = args[2]
    var restored = false
    if let prev = findDevice(uid: prevUID), setDefaultOutput(prev) { restored = true }
    var destroyed = false
    if let mirror = findDevice(uid: MIRROR_UID) {
        // If restore failed (device unplugged mid-recording), fall back to ANY
        // physical output before destroying the mirror — never strand no-default.
        if !restored {
            for d in sysProp(kAudioHardwarePropertyDevices, as: AudioObjectID.self)
            where d != mirror && devString(d, kAudioDevicePropertyDeviceUID) != BLACKHOLE_UID {
                var addr = AudioObjectPropertyAddress(
                    mSelector: kAudioDevicePropertyStreams,
                    mScope: kAudioDevicePropertyScopeOutput,
                    mElement: kAudioObjectPropertyElementMain)
                var size: UInt32 = 0
                if AudioObjectGetPropertyDataSize(d, &addr, 0, nil, &size) == noErr,
                   size > 0, setDefaultOutput(d) { restored = true; break }
            }
        }
        destroyed = destroyWithPoll(mirror)
    }
    print("{\"ok\": \(restored || destroyed), \"restored\": \(restored), " +
          "\"destroyed\": \(destroyed)}")

default:
    fail("unknown command '\(cmd)' (status | engage | release <prev_uid>)")
}
