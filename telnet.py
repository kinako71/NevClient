
import asyncio
import telnetlib3
import csv


class TelnetController:
    def __init__(self, host="localhost", port=9000):
        self.host = host
        self.port = port

        # チャンネルごとに送信するコマンド
        self.requests = [
            "SET AO 0 {channel}",
            "{value} #OK",
        ]

        # 全チャンネルの設定後に送信するコマンド
        self.run_command = "RUN AO *"

        # CSV読み込み（pandas不使用）
        self.channels = []
        self.values = []

        with open(
            "parameters_hokkaido_new.csv",
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as f:
            reader = csv.DictReader(f)

            for row in reader:
                channel = row["#CH"].replace("AO-", "").strip()
                value = row["cl1_freq"].strip()

                self.channels.append(channel)
                self.values.append(value)

        print(f"CSV読み込み完了: {len(self.channels)} チャンネル")

    async def send_request(
        self, reader, writer, request, channel="", value=""
    ):
        formatted_request = request.format(
            channel=channel,
            value=value,
        )

        print(f"送信: {formatted_request}")
        writer.write(formatted_request + "\n")
        await writer.drain()

        # サーバーの応答を待つ
        await asyncio.sleep(0.5)

        response = await reader.read(1024)
        print(f"応答: {response!r}")

        return response

    async def run(self):
        writer = None

        try:
            reader, writer = await telnetlib3.open_connection(
                self.host,
                self.port,
            )
            print(f"{self.host}:{self.port} に接続しました")

            # 各チャンネルに値を設定
            for channel, value in zip(self.channels, self.values):
                print(
                    f"\n--- Channel: {channel}, Value: {value} ---"
                )

                for request in self.requests:
                    await self.send_request(
                        reader,
                        writer,
                        request,
                        channel,
                        value,
                    )

            # 全チャンネルの設定完了後に実行
            print(f"\n送信: {self.run_command}")
            writer.write(self.run_command + "\n")
            await writer.drain()

            await asyncio.sleep(0.5)
            response = await reader.read(1024)
            print(f"応答: {response!r}")

            print("\n全てのリクエストが完了しました")

        except ConnectionRefusedError:
            print("接続が拒否されました")

        except Exception as e:
            print(f"エラーが発生しました: {e}")

        finally:
            if writer is not None:
                writer.close()
                # telnetlib3のTelnetWriterUnicodeには
                # wait_closed() がないため呼び出さない


if __name__ == "__main__":
    controller = TelnetController()
    asyncio.run(controller.run())
