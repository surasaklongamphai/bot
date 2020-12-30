import json
import requests
import time
import datetime

API_HOST = 'https://api.bitkub.com'
API_KEY = '39ff778fefc0dbf946a454186d8d2744'
API_SECRET = b'087b1f367ed83bb10cb7026ccfc64a9c'

url = 'https://notify-api.line.me/api/notify'
token = 'UZEtTWVa3kEuHFRwlYoEdCy5DDbp0T2FlWuqO1dodml'
headers = {'content-type':'application/x-www-form-urlencoded','Authorization':'Bearer '+token}

def json_encode(data):
	return json.dumps(data, separators=(',', ':'), sort_keys=True)
def sign(data):
	j = json_encode(data)
	h = hmac.new(API_SECRET, msg=j.encode(), digestmod=hashlib.sha256)
	return h.hexdigest()

header = {
	'Accept': 'application/json',
	'Content-Type': 'application/json',
	'X-BTK-APIKEY': API_KEY,
}

produc_balances = 'DOGE'
produc_crypto = 'THB_' + produc_balances
LOCK = 0.1

timer = datetime.datetime.now()
start_text_line = str(timer.strftime("\n%d-%m-%Y %H:%M:%S")) + '\nพร้อมใช้งาน BOT DOGE'
requests.post(url, headers=headers, data = {'message':start_text_line})

while True:
        try:
                timer = datetime.datetime.now()
                rticker = requests.get(API_HOST + '/api/market/ticker')
                rticker = rticker.json()
                price_crypto = float(rticker[produc_crypto]['last'])
                max_crypto = float(rticker[produc_crypto]['high24hr'])
                min_crypto = float(rticker[produc_crypto]['low24hr'])
                HighestBid = float(rticker[produc_crypto]['highestBid'])
                LowestAsk = float(rticker[produc_crypto]['lowestAsk'])
                PercentChange = float(rticker[produc_crypto]['percentChange'])

                if price_crypto > LOCK:
                        start_text_line = str(timer.strftime("\n%d-%m-%Y %H:%M:%S")) + '\n DOGE COIN' + STR(price_crypto)
                        requests.post(url, headers=headers, data = {'message':start_text_line})
                        LOCK = price_crypto
                        
                print('\n=========DOGE=======')
                print('TIME:', timer.strftime("%d-%m-%Y %H:%M:%S"))
                print('Price:', price_crypto)
                print('MAX 24H:', max_crypto)
                print('MIN 24H:', min_crypto)
                print('Percent 24H', PercentChange)
                print('HIGH BIDS:', HighestBid)
                print('LOW ASKS', LowestAsk)

        except:
                print ('\nValueError')
        else:
                print ('\nSuccess, no error! <EXT NOW>')
                
        time.sleep(2)









