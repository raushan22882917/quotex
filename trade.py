import os
import time
import asyncio
import pandas as pd
import csv
from datetime import datetime, timedelta
from pathlib import Path
from quotexapi.config import (
    email,
    password,
    email_pass,
    user_data_dir
)
from quotexapi.stable_api import Quotex

client = Quotex(
    email=email,
    password=password,
    lang="en",
    email_pass=email_pass,
    user_data_dir=user_data_dir
)

CSV_FILE = "trade.csv"

async def connect(attempts=5):
    check, reason = await client.connect()
    if not check:
        attempt = 0
        while attempt < attempts:
            if not client.check_connect():
                check, reason = await client.connect()
                if check:
                    print("Reconnected successfully!")
                    break
                else:
                    attempt += 1
                    if Path(os.path.join(".", "session.json")).is_file():
                        Path(os.path.join(".", "session.json")).unlink()
                    print(f"Attempting to reconnect: Attempt {attempt + 1} of {attempts}")
            else:
                break
            await asyncio.sleep(2)
    return check, reason

def format_asset_name(asset_name):
    formatted_name = asset_name.replace("/", "").replace(" (OTC)", "_otc")
    return formatted_name
selected_asset_index = None
async def get_top_asset(profit_threshold_1M=0, profit_threshold_5M=0):
    global selected_asset_index

    async def find_valid_asset(filtered_assets):
        # Loop through the top 3 filtered assets and skip invalid ones
        for index in range(len(filtered_assets)):
            top_asset_name, top_asset_data = filtered_assets[index]
            formatted_asset_name = format_asset_name(top_asset_name)
            if "Bitcoin_otc" not in formatted_asset_name and "USDBRL_otc" not in formatted_asset_name:
                print(f"Automatically Selected Asset: {formatted_asset_name} - 1M Profit: {top_asset_data['profit']['1M']} | 5M Profit: {top_asset_data['profit']['5M']}")
                selected_asset_index = index + 1  # Store the selected index globally
                return formatted_asset_name
            else:
                print(f"Skipping asset: {formatted_asset_name}")
        return None

    # Function to get and filter the top assets
    async def get_filtered_assets():
        check_connect, message = await client.connect()
        if check_connect:
            all_data = client.get_payment()  # Removed await here
            filtered_assets = []

            for asset_name, asset_data in all_data.items():
                if "Bitcoin_otc" in format_asset_name(asset_name) or "USDBRL_otc" in format_asset_name(asset_name):
                    print(f"Skipping asset: {asset_name}")
                    continue

                profit_1M = asset_data["profit"]["1M"]
                profit_5M = asset_data["profit"]["5M"]
                is_open = asset_data["open"]

                if (profit_1M >= profit_threshold_1M or profit_5M >= profit_threshold_5M) and is_open:
                    filtered_assets.append((asset_name, asset_data))

            filtered_assets.sort(key=lambda x: x[1]["profit"]["1M"], reverse=True)

            # Limit to the top 3 assets
            if len(filtered_assets) > 3:
                filtered_assets = filtered_assets[:3]

            return filtered_assets
        return []

    # First attempt to find a valid asset
    filtered_assets = await get_filtered_assets()

    if filtered_assets:
        valid_asset = await find_valid_asset(filtered_assets)
        if valid_asset:
            return valid_asset

    # If no valid asset found, retry the same list from index 1 to 3
    print("No valid asset found in the first attempt. Retrying from index 1 to 3...")
    filtered_assets = await get_filtered_assets()

    if filtered_assets:
        valid_asset = await find_valid_asset(filtered_assets)
        if valid_asset:
            return valid_asset

    print("No valid asset found after retrying.")
    client.close()
    return None



async def get_balance():
    await client.connect()
    balance = await client.get_balance()
    client.close()
async def get_moving_average(asset: str, period: int):
    check_connect, message = await client.connect()
    if check_connect:
        offset = 86400  # in seconds (1 day)
        period_in_seconds = 60  # in seconds for each candle

        # Fetch candle data
        end_from_time = time.time()
        candles = await client.get_candles(asset, end_from_time, offset, period_in_seconds)

        # Calculate moving average
        if candles["data"]:
            prices = [candle['close'] for candle in candles["data"]]
            moving_average = sum(prices[-period:]) / period if len(prices) >= period else None
            
            if moving_average:
                return moving_average
            else:
                print("Not enough data to calculate moving average.")
        else:
            print("No candle data received.")
    print("Exiting...")
    client.close()
    return None

async def get_candle(asset_name):
    check_connect, message = await client.connect()

    if check_connect:
        print(f"Selected Asset: {asset_name}")

        is_open = await client.check_asset_open(asset_name)
        if not is_open:
            return None

        prediction = "HOLD"
        
        # Get moving average from the new method
        moving_average = await get_moving_average(asset_name, period=9)
        if moving_average is None:
            return prediction  # Exit if we cannot calculate the moving average

        # Get recent candle data
        offset = 86400  
        period = 60     
        end_from_time = time.time()

        candles = await client.get_candles(asset_name, end_from_time, offset, period)
        closing_prices = []

        if "data" in candles:
            for candle in candles["data"]:
                if 'close' in candle and 'time' in candle:
                    time_stamp = candle['time']
                    close_price = candle['close']
                    closing_prices.append({'time': time_stamp, 'close': close_price})
                else:
                    print("Candle data is missing 'close' or 'time' key, skipping this entry.")

            if not closing_prices:
                print("No valid candle data found.")
                return None

            current_price = closing_prices[-1]['close']

            if current_price > moving_average:
                prediction = "CALL"
                print(f"Prediction: CALL (Current Price: {current_price} is above Moving Average: {moving_average})")
            elif current_price < moving_average:
                prediction = "PUT"
                print(f"Prediction: PUT (Current Price: {current_price} is below Moving Average: {moving_average})")
            else:
                prediction = "HOLD"
                print(f"Prediction: HOLD (Current Price: {current_price} is equal to Moving Average: {moving_average})")

            return prediction

        else:
            print("Failed to retrieve candle data for the asset.")
            return None
    else:
        print("Failed to connect.")

    client.close()  # Make sure to use await if the close method is asynchronous
    return None


def initialize_csv():
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, mode='w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(["Asset Name", "Amount", "Direction", "Result", "Profit/Loss", "Trade Time"])

def save_trade_result(asset_name, amount, direction, result, profit_loss, trade_time):
    with open(CSV_FILE, mode='a', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([asset_name, amount, direction, result, profit_loss, trade_time])

initialize_csv()

async def buy_and_check_win(amount, asset_name, direction, duration):
    status, buy_info = await client.buy(amount, asset_name, direction, duration)
    trade_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if status:
        print("Waiting for result...")
        trade_result = await client.check_win(buy_info["id"])
        print(f"Result:------------------------- {trade_result}")
        profit = client.get_profit()
        print(f"Profit result------------------------->>>>>>>>>>>    {profit}")

        # Handle win, loss, and draw conditions
        if trade_result and profit > 0:
            print(f"\nWin!!! Profit: R$ {profit}")
            save_trade_result(asset_name, amount, direction, "win", profit, trade_time)
            return "win"
        elif profit < 0:
            print(f"\nLoss!!! Loss: R$ {profit}")
            save_trade_result(asset_name, amount, direction, "loss", profit, trade_time)
            return "loss"
        else:
            # Draw condition: profit is zero
            print(f"\nDraw!!! No Profit/Loss: R$ {profit}")
            save_trade_result(asset_name, amount, direction, "draw", profit, trade_time)
            return "draw"
    else:
        print("Operation failed!!!")
        return "fail"




import random  # Import random to help select between options

import asyncio
import random
from datetime import datetime, timedelta

async def buy_simple():
    check_connect, message = await client.connect()

    if check_connect:
        print("Successfully connected to the client.")
        amounts = []
        max_steps = int(input("Enter the maximum number of steps: "))
        print(f"Maximum steps set to {max_steps}.")

        for step in range(max_steps):
            try:
                amount = float(input(f"Enter amount for step {step + 1}: "))
                if amount <= 0:
                    raise ValueError("Amount must be greater than zero.")
                amounts.append(amount)
                print(f"Amount for step {step + 1} set to {amount}.")
                await asyncio.sleep(1)
            except ValueError as e:
                print(f"Invalid input: {e}. Please enter a valid amount.")
                continue

        duration = 20
        index = 0

        # Asset index and list setup
        selected_asset_index = 1
        assets_list = [1, 2, 3]  # The asset indices to cycle through
        selected_asset = await get_top_asset(selected_asset_index)
        print(f"Starting with asset {selected_asset} (Index: {selected_asset_index})")

        while index < len(amounts):
            current_amount = amounts[index]  # Take the exact amount from the input
            now = datetime.now()
            next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
            wait_seconds = (next_minute - datetime.now()).total_seconds()

            print(f"Waiting for the next trade at {next_minute.strftime('%H:%M:%S')}...")
            await asyncio.sleep(wait_seconds)

            # Calculate prediction for the current trade
            prediction = await get_candle(selected_asset)
            print(f"Prediction for {selected_asset}: {prediction}")

            if prediction in ["CALL", "PUT"]:
                trade_parts = 1  # Default to a single trade
                if 3001 <= current_amount <= 6000:
                    trade_parts = 2
                elif 6000 < current_amount <= 8000:
                    trade_parts = 3

                part_amount = current_amount / trade_parts
                total_profit_loss = 0
                overall_result = "draw"

                print(f"Trading {prediction.lower()} with total amount: {current_amount} (split into {trade_parts} parts, each {part_amount})")

                for part in range(trade_parts):
                    print(f"Placing part {part + 1}/{trade_parts} of trade with amount: {part_amount}")

                    if await client.check_asset_open(selected_asset):
                        result = await buy_and_check_win(part_amount, selected_asset, prediction.lower(), duration)
                        print(f"Trade result for part {part + 1}: {result}")

                        part_profit = client.get_profit()  # Assuming this gets the last trade's profit
                        total_profit_loss += part_profit

                        if result == "win" and overall_result != "loss":
                            overall_result = "win"
                        elif result == "loss":
                            overall_result = "loss"
                    else:
                        print(f"ERROR: Asset {selected_asset} is closed.")
                        return

                print(f"Total profit/loss for combined trade: {total_profit_loss}")

                # After trades, determine whether to move to the next amount or reset
                if overall_result == "win":
                    # Win: Reset to the first amount and switch asset
                    index = 0
                    selected_asset_index = random.choice([i for i in assets_list if i != selected_asset_index])
                    selected_asset = await get_top_asset(selected_asset_index)
                    print(f"Win! Resetting to first amount and switching to new asset index: {selected_asset_index} -> Asset: {selected_asset}")

                elif overall_result == "loss":
                    # Loss: Move to the next amount in the list
                    index += 1
                    if index < len(amounts):
                        print(f"Loss! Moving to next amount: {amounts[index]}")
                    else:
                        print("No more amounts available. Exiting due to consecutive losses.")
                        return

            else:
                # No valid prediction, continue with the next asset
                print("No valid prediction available. Choosing next asset.")
                selected_asset_index = random.choice([i for i in assets_list if i != selected_asset_index])
                selected_asset = await get_top_asset(selected_asset_index)
                print(f"Switched to new asset index: {selected_asset_index} -> Asset: {selected_asset}")
                continue  # Retry with the next asset

    else:
        print(f"Failed to connect to the client. Message: {message}")

    print("Exiting...")
    try:
        await client.close()  # Ensure proper closure with await
        print("Client closed successfully.")
    except Exception as e:
        print(f"Error closing the client: {e}")




async def main():
    await connect()
    await buy_simple()

# Run the main function
if __name__ == "__main__":
    asyncio.run(main())