# Implementation Plan: Fix Timer Loop Dictionary Modification Bug

## Problem Summary

The [`fun()`](bot.py:281-343) task loop crashes when it tries to modify the `timersKey` dictionary while iterating over it. This is the root cause of your timer checking stopping.

**Current Buggy Code (lines 291-302):**
```python
for keys in timersKey:
    list = timersKey.get(keys)
    readyCheck = (time.time() / 60) - list[1]
    
    if (readyCheck >= list[0]):
        print(keys.name + 'is ready')
        timersKey.pop(keys)  # ❌ RuntimeError: dictionary changed size during iteration
        print(timersKey)
        channel = list[2]
        await channel.send(f"{keys.mention} needs to start playing {DEFAULT_GAME} right fucking now")
        return  # ❌ Also prevents multiple timers from expiring in same iteration
```

---

## Solution: Two-Pass Approach

### Step 1: Identify Expired Timers (First Pass)
Loop through all timers and collect which ones have expired into a separate list.

### Step 2: Process Expired Timers (Second Pass)
Loop through the list of expired timers, send notifications, and remove them from the dictionary.

---

## Code Changes Required

### File: [`bot.py`](bot.py)

**Location:** Lines 281-305 (the [`fun()`](bot.py:281) function timer checking section)

**Replace:**
```python
# Check all timers for end time
for keys in timersKey:
    list = timersKey.get(keys)
    readyCheck = (time.time() / 60) - list[1]

    # if the timer is up send message to same guild it came from. (using context in dict)
    if (readyCheck >= list[0]):
        print(keys.name + 'is ready')
        timersKey.pop(keys)  # detele the dictionary value
        print(timersKey)
        channel = list[2]  # get contect from dictionary
        await channel.send(f"{keys.mention} needs to start playing {DEFAULT_GAME} right fucking now")  # send message
        return
    else:
        print(readyCheck)
```

**With:**
```python
# Check all timers for end time
# First pass: identify expired timers
expired_timers = []
for keys in timersKey:
    list = timersKey.get(keys)
    readyCheck = (time.time() / 60) - list[1]

    # if the timer is up, add to expired list
    if (readyCheck >= list[0]):
        expired_timers.append(keys)
    else:
        print(readyCheck)

# Second pass: process and remove expired timers
for keys in expired_timers:
    list = timersKey.get(keys)
    print(keys.name + ' is ready')
    timersKey.pop(keys)  # delete the dictionary value
    print(timersKey)
    channel = list[2]  # get context from dictionary
    await channel.send(f"{keys.mention} needs to start playing {DEFAULT_GAME} right fucking now")  # send message
```

---

## What This Fixes

### 1. **Prevents RuntimeError**
- No longer modifying the dictionary during iteration
- The loop will not crash with "dictionary changed size during iteration"

### 2. **Handles Multiple Simultaneous Timers**
- Removed the `return` statement that was exiting early
- All expired timers in the same iteration will now be processed
- If 3 timers expire at the same time, all 3 users get notified

### 3. **Maintains Existing Behavior**
- Same notifications are sent
- Same dictionary cleanup happens
- Same debug print statements remain
- No changes to other parts of the loop (Spotify token refresh, playlist management)

---

## Testing Checklist

After implementing this fix:

- [ ] **Single timer test**: Set one timer with `/giveme 1` and verify notification arrives
- [ ] **Multiple timer test**: Have 2-3 users set timers for the same time and verify all get notified
- [ ] **Loop stability test**: Run bot for several hours with periodic timers to ensure no crashes
- [ ] **Verify timer removal**: Use `/alltimers` before and after expiration to confirm cleanup

---

## Implementation Steps

1. Open [`bot.py`](bot.py)
2. Locate the [`fun()`](bot.py:281) function (starts at line 281)
3. Find the timer checking loop (lines 291-305)
4. Replace the code as specified above
5. Save the file
6. Restart the bot
7. Test with the checklist above

---

## Additional Notes

- This is a **minimal, surgical fix** that addresses the core issue
- No changes to function signatures or global variables
- No changes to how timers are created or queried
- The fix is backward compatible with existing timer data
- Other potential improvements (error handling, loop monitoring, etc.) can be added later if needed
