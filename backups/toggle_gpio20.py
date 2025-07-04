import lgpio
import time

PIN = 20

def release_all_gpio():
    released = []
    for chip in range(4):  # Most Pis/chips are numbered 0 to 3
        try:
            lgpio.gpiochip_close(chip)
            released.append(chip)
        except Exception:
            # Ignore if not open or already closed
            pass
    if released:
        print(f"Released GPIO chips: {released}")
    else:
        print("No GPIO chips were open or needed releasing.")

if __name__ == "__main__":
    release_all_gpio()


# Clean up any previous claims on the pin at script start
try:
    lgpio.gpiochip_close(0)
except Exception:
    pass  # Ignore if nothing to close

# Open first GPIO chip
h = lgpio.gpiochip_open(0)

# Claim pin as output
lgpio.gpio_claim_output(h, PIN)

print("Setting GPIO 20 INACTIVE (LOW)")
lgpio.gpio_write(h, PIN, 0)
time.sleep(5)

print("Setting GPIO 20 ACTIVE (HIGH)")
lgpio.gpio_write(h, PIN, 1)
time.sleep(5)

# Clean up at script end
lgpio.gpiochip_close(h)
print("Done.")

