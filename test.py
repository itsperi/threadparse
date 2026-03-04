import threading

counter = 0
lock = threading.Lock()

def worker():
    global counter
    counter += 1
    print(counter)

for _ in range(100):
   t = threading.Thread(target=worker)
   t.start()
   
