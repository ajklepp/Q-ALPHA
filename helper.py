import os
from dotenv import load_dotenv
load_dotenv()
api_key = os.getenv(chr(80)+chr(79)+chr(76)+chr(89)+chr(71)+chr(79)+chr(78)+chr(95)+chr(65)+chr(80)+chr(73)+chr(95)+chr(75)+chr(69)+chr(89))
if not api_key:
    print(chr(69)+chr(82)+chr(82)+chr(79)+chr(82))
    exit()
print(api_key[:8])
from polygon import RESTClient
client=RESTClient(api_key)
bars=client.get_aggs(chr(65)+chr(65)+chr(80)+chr(76),1,chr(100)+chr(97)+chr(121),chr(50)+chr(48)+chr(50)+chr(52)+chr(45)+chr(48)+chr(49)+chr(45)+chr(48)+chr(49),chr(50)+chr(48)+chr(50)+chr(52)+chr(45)+chr(48)+chr(49)+chr(45)+chr(49)+chr(48))
print(len(bars))
print(bars[0].close)
