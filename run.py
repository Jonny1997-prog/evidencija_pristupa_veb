from app import app, init_db

# Ovo se izvršava pri svakom startu aplikacije (IIS ili lokalno)
init_db()

# Ovo se izvršava samo kada pokrećeš lokalno sa: python run.py
if __name__ == "__main__":
  #  app.run(host="0.0.0.0", port=8086, debug=True)
  app.run(host="0.0.0.0", port=5000, debug=True)




