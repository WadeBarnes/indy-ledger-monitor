import plugin_collection
import json
import os
import datetime
import time
from .google_sheets import gspread_authZ
from fetch_ledger_tx import get_txn_range

class main(plugin_collection.Plugin):
    
    def __init__(self):
        super().__init__()
        self.index = 1
        self.name = 'Sovrin Network Metrics'
        self.description = ''
        self.type = ''
        self.gauth_json = None
        self.file_name = None
        self.worksheet_name = None
        self.batchsize = None

    def parse_args(self, parser):
        parser.add_argument("--mlog", action="store_true", help="Metrics log argument uses google sheets api and requires, Google API Credentials json file name (file must be in root folder), google sheet file name and worksheet name. ex: --mlog --batchsize [Number (Not Required)] --json [Json File Name] --file [Google Sheet File Name] --worksheet [Worksheet name]")
        parser.add_argument("--json", default=os.environ.get('JSON') , help="Google API Credentials json file name (file must be in root folder). Can be specified using the 'JSON' environment variable.", nargs='*')
        parser.add_argument("--file", default=os.environ.get('FILE') , help="Specify which google sheets file you want to log too. Can be specified using the 'FILE' environment variable.", nargs='*')
        parser.add_argument("--worksheet", default=os.environ.get('WORKSHEET') , help="Specify which worksheet you want to log too. Can be specified using the 'WORKSHEET' environment variable.", nargs='*')
        parser.add_argument("--batchsize", default=int(os.environ.get('BATCHSIZE') or '0') , help="Specify the read/write batch size. Not Required. Default is 10. Can be specified using the 'STORELOGS' environment variable.")

    def load_parse_args(self, args):
        global verbose
        verbose = args.verbose
        # Support names and paths containing spaces.
        # Other workarounds including the standard of putting '"'s around values containing spaces does not always work.
        if args.json:
            args.json = ' '.join(args.json)
        if args.file:
            args.file = ' '.join(args.file)
        if args.worksheet:
            args.worksheet = ' '.join(args.worksheet)

        if args.mlog:
            if args.json and args.file and args.worksheet:
                self.enabled = args.mlog
                self.gauth_json = args.json
                self.file_name = args.file
                self.worksheet_name = args.worksheet
                self.batchsize = args.batchsize
            else:
                print('Metrics log argument uses google sheets api and requires, Google API Credentials json file name (file must be in root folder), google sheet file name and worksheet name.')
                print('ex: --mlog  --batchsize [Number (Not Required)] --json [Json File Name] --file [Google Sheet File Name] --worksheet [Worksheet name]')
                exit()
        
    async def perform_operation(self, result, pool, network_name):
        txn_range = [None] * 2
        logging_range = [None] * 2 
        int_batchsize = int(self.batchsize)
        MAX_BATCH_SIZE = 100
        BATCHSIZE = 10 # Default amount of txn to fetch from pool

        last_logged_txn = [self.find_last()]                                # Get last txn that was logged
        txn_range[0] = last_logged_txn[0] + 1                               # Get the next txn: txn_range[*next_txn, ledger_size]
        maintxr_response = await get_txn_range(pool, list(last_logged_txn)) # Run the last logged txn to get ledger size
        txn_range[1] = maintxr_response[0]["data"]["ledgerSize"]            # Get ledger size: txn_range[next_txn, *ledger_size]
        num_of_new_txn = txn_range[1] - txn_range[0] + 1                    # Find how many new txn there are.

        if num_of_new_txn == 0:                                             # If no new txn exit()
            print("No New Transactions. Exiting...") 
            exit()
        
        if int_batchsize == 0:
            print(f'Number of stored logs not specifed. Storing {BATCHSIZE} logs if avalible.')
        elif int_batchsize > MAX_BATCH_SIZE:
            BATCHSIZE = MAX_BATCH_SIZE
            print(f'The reqested batch size ({int_batchsize}) is to large. Setting to {BATCHSIZE}.')
        else:
            BATCHSIZE = int_batchsize
            print(f'Storing {BATCHSIZE} logs if avalible.')

        if num_of_new_txn < BATCHSIZE:                                     # Run to the end of the new txn if its less then the log interval
            logging_range[0] = txn_range[0]
            logging_range[1] = txn_range[1] + 1
        else:                                                               # Set log interval to only grab a few txn's at a time if there are more txn then batchsize
            logging_range[0] = txn_range[0]
            logging_range[1] = txn_range[0] + BATCHSIZE

        #------------------- Below won't run unless there are new txns ----------------------------------

        print(f'{num_of_new_txn} new transactions. Last transaction logged: {last_logged_txn[0]} Transaction Range: {txn_range[0]}-{txn_range[1]}')
        while True:
            print(f'Logging transactions {logging_range[0]}-{logging_range[1]-1}')
            maintxr_response = await get_txn_range(pool, list(range(logging_range[0],logging_range[1])))
            txn_seqNo = self.metrics(maintxr_response, network_name, txn_range)
            if txn_seqNo == txn_range[1]:
                break
            logging_range[0] = txn_seqNo + 1
            logging_range[1] = txn_seqNo + BATCHSIZE + 1 # put if here to have end of txxn if at end
            exit()
        print(f'{txn_seqNo}/{txn_range[1]} Transactions logged! {num_of_new_txn} New Transactions. Done!')
        return result


    def find_last(self):
        authD_client = gspread_authZ(self.gauth_json)
        sheet = authD_client.open(self.file_name).worksheet(self.worksheet_name)
        first_row = sheet.row_values(2)
        if not first_row:
            sheet.delete_row(2)
            self.find_last()
        else:
            last = int(first_row[0]) # returns as str casting to int
        return last

    def metrics(self, maintxr_response, network_name, txn_range):
        authD_client = gspread_authZ(self.gauth_json)
        try:
            sheet = authD_client.open(self.file_name).worksheet(self.worksheet_name) # Open sheet
        except:
            print("\033[1;31;40mUnable to upload data to sheet! Please check file and worksheet name and try again.")
            print(f'File name entered: {self.file_name}. Worksheet name entered: {self.worksheet_name}.\033[m')
            exit()
        num_of_txn = 0

        for txn in maintxr_response:
            REVOC_REG_ENTRY = 0
            REVOC_REG_DEF = 0 
            CLAIM_DEF = 0
            NYM = 0
            ATTRIB = 0
            SCHEMA = 0

            txn_seqNo = txn["seqNo"]
            txn_type = txn["data"]["txn"]["type"]
            
            if 'txnTime' in txn["data"]["txnMetadata"]:
                txn_time_epoch = txn["data"]["txnMetadata"]["txnTime"]
                txn_time = datetime.datetime.fromtimestamp(txn_time_epoch).strftime('%Y-%m-%d %H:%M:%S') # formated to 12-3-2020 21:27:49
                txn_date = datetime.datetime.fromtimestamp(txn_time_epoch).strftime('%Y-%m-%d') # formated to 12-3-2020
            else:
                txn_time, txn_date = "", ""
            
            if 'endorser' in txn["data"]["txn"]["metadata"]:
                endorser = txn["data"]["txn"]["metadata"]["endorser"]
            else:
                endorser = ""
            
            if 'from' in txn["data"]["txn"]["metadata"]:
                txn_from = txn["data"]["txn"]["metadata"]["from"]
            else:
                txn_from = ""

            if txn_type == '1':
                txn_type = 'NYM'
                NYM = 1
            elif txn_type == '100':
                txn_type = 'ATTRIB'
                ATTRIB = 1
            elif txn_type == '101':
                txn_type = 'SCHEMA'
                SCHEMA = 1
            elif txn_type == '102':
                txn_type = 'CLAIM_DEF'
                CLAIM_DEF = 1
            elif txn_type == '113':
                txn_type = 'REVOC_REG_DEF'
                REVOC_REG_DEF = 1
            elif txn_type == '114':
                txn_type = 'REVOC_REG_ENTRY'
                REVOC_REG_ENTRY = 1
            elif txn_type == '200':
                txn_type = 'SET_CONTEXT'
            elif txn_type == '0': 
                txn_type = 'NODE'
            elif txn_type == '10':
                txn_type = 'POOL_UPGRADE'
            elif txn_type == '11':
                txn_type = 'NODE_UPGRADE'
            elif txn_type == '11':
                txn_type = 'POOL_CONFIG'
            elif txn_type == '12':
                txn_type = 'AUTH_RULE'
            elif txn_type == '12':
                txn_type = 'AUTH_RULES'
            elif txn_type == '4': 
                txn_type = 'TXN_AUTHOR_AGREEMENT'
            elif txn_type == '5': 
                txn_type = 'TXN_AUTHOR_AGREEMENT_AML'
            elif txn_type == '20000': 
                txn_type = 'SET_FEES'
            else:
                print("error")
            
            row = [txn_seqNo, txn_type, txn_time, endorser, txn_from, txn_date, REVOC_REG_ENTRY, REVOC_REG_DEF, CLAIM_DEF, NYM, ATTRIB, SCHEMA]
            print(row)
            sheet.insert_row(row, 2,value_input_option='USER_ENTERED')
            num_of_txn += 1
            if txn_seqNo == txn_range[1]:
                break
            # This is to make sure we don't run into the google api rate limit.
            time.sleep(2)

        print(f'\033[1;92;40m{num_of_txn} transactions added to {self.file_name} in sheet {self.worksheet_name}.\033[m')
        return txn_seqNo