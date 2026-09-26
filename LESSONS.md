# LESSONS — 111 TRANSCRIBE

## 26.9.2026, v4: two Android faults, both found on the Pixel 7 emulator

**The recorder said, in red, "Mikrofon nedostupan: Permission denied."** The old recorder was a
hand-made iframe that called `getUserMedia` the moment the page loaded, not when REC was pressed.
On Android Chrome a prompt nobody asked for is easily dismissed; "Block" is remembered for the
site, and after a few dismissals Chrome stops asking and refuses on its own. The recorder never
asked again. Safari on iOS asks every time, which is why the iPhone never showed it. There is a
second door on Android: the operating system asks once whether *Chrome* may record audio at all,
and "Don't allow" there gives the same error on every site. The fix is `st.audio_input`: it asks
only when the button is pressed, and it hands the audio to Python, which is what lets Stop start
the transcription.

**The Upload button offered apps instead of files on some phones.** The uploader carried a list
of fifty extensions, which becomes the file input's `accept` attribute. Chrome turns that into a
set of MIME types for Android, and some makers' builds answer a mixed audio and video set with an
app chooser (recorder, camera, gallery). Stock Android shows the file browser either way, so the
emulator could not reproduce it; the cause was read from the attribute itself. The uploader has no
filter now, and ffmpeg is the judge, as it always was. If a phone still will not hand over a
file, the link fallback fetches it on the server.

**Streamlit forgets a widget that is not drawn.** With the settings behind the gear, every choice
went back to its default when the gear closed. Writing each key back to itself at the top of the
run keeps it. The derived values (`_lang_choice` and the rest) are also computed every run, so a
recording made before the gear was ever opened still goes out in Hrvatski with speakers detected.

**The emulator's microphone is silent** (peak 0.0 with `avd hostmicon` on), so the recording path
was proven in desktop Chrome with a spoken WAV as the fake microphone: stop, and 5.8 s later the
transcript. A real phone's thumb and microphone are still the last test.

## 26.9.2026, v6: the number is a whole number

v4 and v5 were pushed as "v4.0" and "v4.1". MANTRA_MANIFEST `modules/versioning.md` says one whole
number, a new one for every change, and never a dot. They keep the names they were pushed with,
because a pushed name is not rewritten; the count carries on from them, and this build is v6.
