#!/bin/bash
export JAVA_HOME=/opt/homebrew/opt/openjdk@17
export ANDROID_HOME=$HOME/Library/Android/sdk
export PATH=$JAVA_HOME/bin:/opt/homebrew/bin:$ANDROID_HOME/platform-tools:$PATH
cd "$(dirname "$0")"
npx expo run:android "$@"
