import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import smtplib
from email.mime.text import MIMEText

# Hardcoded list of ASX200 symbols (add .AX for yfinance). Update as needed from ASX sources.
asx200_symbols = [
    'BHP.AX', 'CBA.AX', 'CSL.AX', 'RIO.AX', 'NAB.AX', 'WBC.AX', 'ANZ.AX', 'MQG.AX', 'WES.AX', 'GMG.AX',
    'WDS.AX', 'FMG.AX', 'TLS.AX', 'ALL.AX', 'REA.AX', 'WOW.AX', 'TCL.AX', 'QBE.AX', 'STO.AX', 'COL.AX',
    'SYD.AX', 'XRO.AX', 'COH.AX', 'S32.AX', 'RMD.AX', 'BXB.AX', 'JHX.AX', 'NCM.AX', 'SUN.AX', 'ORG.AX',
    'IAG.AX', 'FPH.AX', 'AMC.AX', 'CPU.AX', 'SHL.AX', ' PME.AX', 'NST.AX', 'SCG.AX', 'TLX.AX', ' PME.AX',
    'TPG.AX', ' PME.AX', ' PME.AX', ' PME.AX', ' PME.AX', ' PME.AX', ' PME.AX', ' PME.AX', ' PME.AX', ' PME.AX',
    # ... (abbreviated for brevity; add the full ~200 from ASX CSV or sites like marketindex.com.au/asx200)
    # Example continuation: 'AMP.AX', 'A2M.AX', 'ALQ.AX', 'ALU.AX', 'ALX.AX', 'APA.AX', 'APX.AX', 'AST.AX', 'ASX.AX', 'AWC.AX',
    # 'BEN.AX', 'BKW.AX', 'BLD.AX', 'CAR.AX', 'CDA.AX', 'CEN.AX', 'CGF.AX', 'CHC.AX', 'CQE.AX', 'CWN.AX',
    # ' DMP.AX', 'DOW.AX', 'EDV.AX', 'FLT.AX', 'GMG.AX', 'GPT.AX', 'HVN.AX', 'ILU.AX', 'IOO.AX', 'IPH.AX',
    # 'JBH.AX', 'LLC.AX', 'LTR.AX', 'LYC.AX', 'MGX.AX', 'MIN.AX', 'MPL.AX', 'MP1.AX', 'MTS.AX', 'NHF.AX',
    # 'NIC.AX', 'NVX.AX', ' NXT.AX', 'ORA.AX', 'ORI.AX', 'OSH.AX', 'PBH.AX', 'PDL.AX', 'PLS.AX', 'PMV.AX',
    # 'QAN.AX', 'QUB.AX', 'RBD.AX', 'RWC.AX', 'SDF.AX', 'SEK.AX', 'SFR.AX', 'SGP.AX', 'SLK.AX', 'SOL.AX',
    # 'SQ2.AX', 'SVW.AX', 'TAH.AX', 'TNE.AX', 'TPW.AX', 'TYR.AX', 'VUK.AX', 'WHC.AX', 'WOR.AX', 'ZIP.AX',
    # Complete the list to 200 for full scan. For now, this is a sample to demonstrate.
]  # Full list ~200; replace with your own or fetch dynamically.

def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calculate_demarker(high, low, period=14):
    demax = high - high.shift(1)
    demax[demax < 0] = 0
    demin = low.shift(1) - low
    demin[demin < 0] = 0
    sma_demax = demax.rolling(window=period).mean()
    sma_demin = demin.rolling(window=period).mean()
    demarker = sma_demax / (sma_demax + sma_demin)
    return demarker

def find_swing_low_high(close, lookback=20):
    # Simple swing detection: recent low and high
    swing_high = close.rolling(window=lookback).max().iloc[-1]
    swing_low = close.rolling(window=lookback).min().iloc[-1]
    return swing_low, swing_high

def check_signal(symbol):
    # Get data
    data = yf.download(symbol, period='3mo', interval='1d')
    if data.empty:
        return None
    # Flatten MultiIndex columns from yfinance
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.droplevel(1)

    # Calculate indicators
    data['EMA8'] = calculate_ema(data['Close'], 8)
    data['EMA21'] = calculate_ema(data['Close'], 21)
    data['DeMarker'] = calculate_demarker(data['High'], data['Low'])

    latest = data.iloc[-1]
    previous = data.iloc[-2]

    # Trend check
    if latest['Close'] <= latest['EMA21'] or latest['EMA8'] <= latest['EMA21']:
        return None

    # Pullback and bounce check
    if previous['Close'] <= previous['EMA8'] and latest['Close'] > latest['EMA8']:
        # DeMarker bounce
        if previous['DeMarker'] < 0.3 and latest['DeMarker'] > 0.35:
            # Calculate Fib extensions
            swing_low, swing_high = find_swing_low_high(data['Close'])
            fib_range = swing_high - swing_low
            target1 = latest['Close'] + fib_range * 0.272  # 127.2% extension
            target2 = latest['Close'] + fib_range * 0.618  # 161.8% extension

            return f"BUY SIGNAL for {symbol}: Price {latest['Close']:.2f}, Target1 {target1:.2f} (127.2%), Target2 {target2:.2f} (161.8%). Stop below EMA21 {latest['EMA21']:.2f}."

    return None

def send_email(message):
    # Uncomment and configure for email alerts
    # from_email = 'your_email@gmail.com'
    # to_email = 'your_email@gmail.com'
    # password = 'your_password'
    # msg = MIMEText(message)
    # msg['Subject'] = 'ASX200 Trading Signal'
    # msg['From'] = from_email
    # msg['To'] = to_email
    # server = smtplib.SMTP('smtp.gmail.com', 587)
    # server.starttls()
    # server.login(from_email, password)
    # server.sendmail(from_email, to_email, msg.as_string())
    # server.quit()
    pass

if __name__ == "__main__":
    signals = []
    for symbol in asx200_symbols:
        signal = check_signal(symbol)
        if signal:
            signals.append(signal)
    
    if signals:
        message = "\n".join(signals)
        print(message)
        # send_email(message)
    else:
        print("No signals today.")
