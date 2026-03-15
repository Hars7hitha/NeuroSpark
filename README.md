# NeuroSpark

Adaptive neurofeedback music player using Arduino EEG.

## Setup
1. `pip install flask pyserial numpy python-dotenv`
2. Copy `.env.example` to `.env` and fill in your keys
3. Add MP3s to `/music/` named `f_mu1.mp3` ... `r_ly5.mp3`
4. Upload Arduino sketch to UNO R4 on A2
5. `python server.py`
6. Open http://localhost:5000

## Music naming
focused instrumental: f_mu1-5.mp3
focused lyrical:      f_ly1-5.mp3
relaxed instrumental: r_mu1-5.mp3
relaxed lyrical:      r_ly1-5.mp3
```

Also create `.env.example` (this one IS uploaded, no real values):
```
SECRET_KEY=your-secret-key-here
