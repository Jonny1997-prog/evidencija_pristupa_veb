from app import app, init_db

init_db()

if __name__ == "__main__":
  #  app.run(host="0.0.0.0", port=8086, debug=True)
  app.run(host="0.0.0.0", port=5000, debug=True)




