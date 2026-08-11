import os
from dotenv import load_dotenv
load_dotenv()
url=os.getenv(chr(83)+chr(85)+chr(80)+chr(65)+chr(66)+chr(65)+chr(83)+chr(69)+chr(95)+chr(85)+chr(82)+chr(76))
key=os.getenv(chr(83)+chr(85)+chr(80)+chr(65)+chr(66)+chr(65)+chr(83)+chr(69)+chr(95)+chr(83)+chr(69)+chr(82)+chr(86)+chr(73)+chr(67)+chr(69)+chr(95)+chr(75)+chr(69)+chr(89))
if not url or not key:
    print(chr(69)+chr(82)+chr(82)+chr(79)+chr(82))
    exit()
print(url[:40])
print(key[:20])
from supabase import create_client
client=create_client(url,key)
print(chr(83)+chr(117)+chr(112)+chr(97)+chr(98)+chr(97)+chr(115)+chr(101)+chr(32)+chr(79)+chr(75))
