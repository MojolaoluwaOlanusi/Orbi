import queue
import json
from pathlib import Path
import sounddevice as sd
import vosk

model_path = str(Path(__file__).parent / "model")
print("Loading model...")
model = vosk.Model(model_path)
rec = vosk.KaldiRecognizer(model, 16000)
q = queue.Queue()

def callback(indata, frames, time, status):
    if status:
        print("STATUS:", status)
    q.put(bytes(indata))

print("Say something...")
try:
    with sd.RawInputStream(samplerate=16000, blocksize=8000, dtype='int16',
                           channels=1, callback=callback):
        while True:
            data = q.get()
            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result())
                if res.get("text"):
                    print(">>", res["text"])
except KeyboardInterrupt:
    print("Done.")