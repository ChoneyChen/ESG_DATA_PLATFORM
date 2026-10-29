"""One current run executed by the unified document queue."""
import argparse,os
from .runtime import execute

def main():
    parser=argparse.ArgumentParser();parser.add_argument('run_id');args=parser.parse_args()
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    result=execute(args.run_id)
    print(__import__('json').dumps({'kind':'result','result':result},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
