import time
from drx import is_cos_active
while True:
    print("COS:", is_cos_active())
    time.sleep(0.2)